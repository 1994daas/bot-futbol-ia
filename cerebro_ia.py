import math
import numpy as np
import pandas as pd
import requests
from scipy.stats import poisson
from xgboost import XGBClassifier
from lightgbm import LGBMClassifier
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, LIGAS
from contexto_vivo import enriquecer_proximos_partidos

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# =====================================================================
# 1. MÓDULO DE AUTO-APRENDIZAJE POST-PARTIDO (LOG-LOSS Y BRIER SCORE)
# =====================================================================
def autoevaluar_y_aprender():
    """
    Compara los pronósticos anteriores del bot con los resultados reales ya finalizados,
    calcula el error matemático (Log-Loss) y ajusta los pesos entre Dixon-Coles y ML.
    """
    print("🔄 1. Ejecutando ciclo de Auto-Aprendizaje (MLOps)...")
    res_pron = supabase.table("pronosticos_bot").select("*").eq("evaluado", False).execute()
    pendientes = res_pron.data or []
    if not pendientes:
        print("   🟢 Todos los pronósticos pasados ya están evaluados y aprendidos.")
        return 0

    aprendidos = 0
    for pr in pendientes:
        pid = pr["partido_id"]
        res_part = supabase.table("partidos").select("*").eq("partido_id", pid).execute()
        if not res_part.data:
            continue
        partido = res_part.data[0]
        if partido["estado"] != "FINALIZADO" or partido["goles_local"] is None:
            continue

        gh, ga = partido["goles_local"], partido["goles_visita"]
        liga = partido["liga_codigo"]

        # Resultado real codificado: 0 = Local, 1 = Empate, 2 = Visita
        real_idx = 0 if gh > ga else (1 if gh == ga else 2)
        y_onehot = [1 if i == real_idx else 0 for i in range(3)]

        # Probabilidades emitidas por el consenso, Dixon-Coles y Machine Learning
        p_cons = [max(0.01, float(pr["prob_1"])), max(0.01, float(pr["prob_x"])), max(0.01, float(pr["prob_2"]))]
        p_dc = [max(0.01, float(pr["dixon_coles_1"])), max(0.01, float(pr["dixon_coles_x"])), max(0.01, float(pr["dixon_coles_2"]))]
        p_ml = [max(0.01, float(pr["ml_ensemble_1"])), max(0.01, float(pr["ml_ensemble_x"])), max(0.01, float(pr["ml_ensemble_2"]))]

        # Cálculo de Log-Loss y Brier Score
        log_loss_total = -math.log(p_cons[real_idx])
        log_loss_dc = -math.log(p_dc[real_idx])
        log_loss_ml = -math.log(p_ml[real_idx])
        brier = sum((p_cons[i] - y_onehot[i]) ** 2 for i in range(3)) / 3.0

        # Actualizar pronóstico como evaluado
        supabase.table("pronosticos_bot").update({
            "evaluado": True,
            "log_loss_error": round(log_loss_total, 4),
            "brier_score_error": round(brier, 4)
        }).eq("partido_id", pid).execute()

        # Ajustar pesos dinámicos en memoria_modelos según quién tuvo menor error
        mem_res = supabase.table("memoria_modelos").select("*").eq("liga_codigo", liga).execute()
        if mem_res.data:
            mem = mem_res.data[0]
            w_dc = float(mem["peso_dixon_coles"])
            w_ml = float(mem["peso_ml_xgboost"])
            n_eval = int(mem["partidos_evaluados"] or 0) + 1
            ll_prom = ((float(mem["log_loss_promedio"] or 1.0) * (n_eval - 1)) + log_loss_total) / n_eval

            # Aprendizaje por refuerzo suave (Learning rate = 0.025)
            if log_loss_dc < log_loss_ml:
                w_dc = min(0.75, w_dc + 0.025)
                w_ml = round(1.0 - w_dc, 3)
            else:
                w_ml = min(0.75, w_ml + 0.025)
                w_dc = round(1.0 - w_ml, 3)

            supabase.table("memoria_modelos").upsert({
                "liga_codigo": liga,
                "peso_dixon_coles": round(w_dc, 3),
                "peso_ml_xgboost": round(w_ml, 3),
                "partidos_evaluados": n_eval,
                "log_loss_promedio": round(ll_prom, 4)
            }).execute()
            aprendidos += 1

    print(f"   🧠 El bot aprendió de {aprendidos} partidos recién terminados y recalibró sus pesos.")
    return aprendidos

# =====================================================================
# 2. INGENIERÍA DE VARIABLES (MEDIAS MÓVILES EXPONENCIALES EWMA + ELO)
# =====================================================================
def construir_estadisticas_equipos(df_hist):
    """
    Recorre el historial cronológico y calcula para cada equipo:
    - Elo Dinámico ajustado por xG (Glicko proxy)
    - EWMA (últimos 6 partidos) de xG a favor, xG en contra, tiros, córners y tarjetas
    - Fecha de su último partido (para calcular horas de descanso reales)
    """
    df_hist = df_hist.sort_values("fecha_utc").copy()
    elo = {}
    historial_eq = {}
    filas_entrenamiento = []

    for _, row in df_hist.iterrows():
        h_id, a_id = row["local_id"], row["visita_id"]
        liga = row["liga_codigo"]
        fecha = pd.to_datetime(row["fecha_utc"])

        elo_h = elo.get(h_id, 1500.0)
        elo_a = elo.get(a_id, 1500.0)

        hist_h = historial_eq.get(h_id, [])
        hist_a = historial_eq.get(a_id, [])

        def resumir_racha(lista_partidos):
            if not lista_partidos:
                return {"xg_f": 1.35, "xg_c": 1.25, "tiros": 11.0, "corners": 4.8, "tarjetas": 2.2, "ult_fecha": None}
            ultimos = lista_partidos[-6:]
            # Pesos exponenciales: los partidos más recientes valen más
            pesos = np.exp(np.linspace(-1.0, 0.0, len(ultimos)))
            pesos /= pesos.sum()
            return {
                "xg_f": float(np.sum([x["xg_f"] * w for x, w in zip(ultimos, pesos)])),
                "xg_c": float(np.sum([x["xg_c"] * w for x, w in zip(ultimos, pesos)])),
                "tiros": float(np.sum([x["tiros"] * w for x, w in zip(ultimos, pesos)])),
                "corners": float(np.sum([x["corners"] * w for x, w in zip(ultimos, pesos)])),
                "tarjetas": float(np.sum([x["tarjetas"] * w for x, w in zip(ultimos, pesos)])),
                "ult_fecha": ultimos[-1]["fecha"]
            }

        st_h = resumir_racha(hist_h)
        st_a = resumir_racha(hist_a)

        desc_h = min(240.0, max(48.0, (fecha - st_h["ult_fecha"]).total_seconds() / 3600.0)) if st_h["ult_fecha"] else 144.0
        desc_a = min(240.0, max(48.0, (fecha - st_a["ult_fecha"]).total_seconds() / 3600.0)) if st_a["ult_fecha"] else 144.0

        gh = float(row["goles_local"] or 0)
        ga = float(row["goles_visita"] or 0)
        xgh = float(row["xg_local"] if row["xg_local"] is not None else gh)
        xga = float(row["xg_visita"] if row["xg_visita"] is not None else ga)
        alt_dif = float(row.get("diferencial_altitud") or 0)

        # Guardar vector de características ANTES del partido para entrenar XGBoost y LightGBM sin trampa (sin data leakage)
        if len(hist_h) >= 2 and len(hist_a) >= 2:
            target_1x2 = 0 if gh > ga else (1 if gh == ga else 2)
            filas_entrenamiento.append({
                "elo_diff": elo_h - elo_a,
                "h_xg_f": st_h["xg_f"], "h_xg_c": st_h["xg_c"],
                "a_xg_f": st_a["xg_f"], "a_xg_c": st_a["xg_c"],
                "h_tiros": st_h["tiros"], "a_tiros": st_a["tiros"],
                "h_corners": st_h["corners"], "a_corners": st_a["corners"],
                "h_tarjetas": st_h["tarjetas"], "a_tarjetas": st_a["tarjetas"],
                "descanso_diff": desc_h - desc_a,
                "altitud_diff": alt_dif,
                "target": target_1x2
            })

        # Actualizar Elo combinando resultado real (60%) + mérito por xG (40%)
        exp_h = 1.0 / (1.0 + 10.0 ** ((elo_a - (elo_h + 45.0)) / 400.0))
        res_real = 1.0 if gh > ga else (0.5 if gh == ga else 0.0)
        res_xg = 1.0 if xgh > (xga + 0.35) else (0.5 if abs(xgh - xga) <= 0.35 else 0.0)
        score_combinado = (0.6 * res_real) + (0.4 * res_xg)

        elo[h_id] = elo_h + 24.0 * (score_combinado - exp_h)
        elo[a_id] = elo_a + 24.0 * ((1.0 - score_combinado) - (1.0 - exp_h))

        # Añadir partido al historial de cada equipo
        hist_h.append({
            "xg_f": xgh, "xg_c": xga,
            "tiros": float(row.get("tiros_local") or 11),
            "corners": float(row.get("corners_local") or 5),
            "tarjetas": float((row.get("amarillas_local") or 2) + (row.get("rojas_local") or 0) * 2),
            "fecha": fecha
        })
        hist_a.append({
            "xg_f": xga, "xg_c": xgh,
            "tiros": float(row.get("tiros_visita") or 9),
            "corners": float(row.get("corners_visita") or 4),
            "tarjetas": float((row.get("amarillas_visita") or 2) + (row.get("rojas_visita") or 0) * 2),
            "fecha": fecha
        })
        historial_eq[h_id] = hist_h
        historial_eq[a_id] = hist_a

    return pd.DataFrame(filas_entrenamiento), elo, historial_eq

# =====================================================================
# 3. MODELO ESTADÍSTICO DIXON-COLES (MATRIZ BIVARIADA DE POISSON)
# =====================================================================
def predecir_dixon_coles(lambda_local, lambda_visita, rho=-0.09):
    """
    Construye la matriz de probabilidades de marcador exacto (0-0 a 6-6)
    aplicando el factor de corrección tau de Dixon-Coles para marcadores bajos.
    """
    max_goles = 7
    matriz = np.zeros((max_goles, max_goles))

    for x in range(max_goles):
        for y in range(max_goles):
            p_base = poisson.pmf(x, lambda_local) * poisson.pmf(y, lambda_visita)
            # Corrección tau de Dixon-Coles para empates cortos y ventajas mínimas
            if x == 0 and y == 0:
                tau = 1.0 - (lambda_local * lambda_visita * rho)
            elif x == 0 and y == 1:
                tau = 1.0 + (lambda_local * rho)
            elif x == 1 and y == 0:
                tau = 1.0 + (lambda_visita * rho)
            elif x == 1 and y == 1:
                tau = 1.0 - rho
            else:
                tau = 1.0
            matriz[x, y] = max(0.0, p_base * tau)

    matriz /= matriz.sum()

    prob_1 = float(np.sum(np.tril(matriz, -1)))
    prob_x = float(np.sum(np.diag(matriz)))
    prob_2 = float(np.sum(np.triu(matriz, 1)))

    # Probabilidad de Over 2.5 Goles y Ambos Anotan (BTTS)
    prob_over25 = float(sum(matriz[i, j] for i in range(max_goles) for j in range(max_goles) if (i + j) >= 3))
    prob_btts = float(sum(matriz[i, j] for i in range(1, max_goles) for j in range(1, max_goles)))

    # Marcador exacto más probable
    idx_max = np.unravel_index(np.argmax(matriz), matriz.shape)
    marcador_top = f"{idx_max[0]}-{idx_max[1]}"
    prob_marcador_top = float(matriz[idx_max])

    return prob_1, prob_x, prob_2, prob_over25, prob_btts, marcador_top, prob_marcador_top

# =====================================================================
# 4. ENTRENAMIENTO DEL ENSEMBLE ML + GENERACIÓN DE PRONÓSTICOS
# =====================================================================
def ejecutar_cerebro_completo():
    # 1. Enriquecer próximos partidos con clima y alineaciones
    enriquecer_proximos_partidos()

    # 2. Evaluar partidos pasados y aprender de los errores
    autoevaluar_y_aprender()

    # 3. Cargar todo el historial finalizado de Supabase
    print("\n📊 2. Cargando los partidos históricos desde Supabase para entrenar...")
    res_hist = supabase.table("partidos").select("*").eq("estado", "FINALIZADO").limit(2000).execute()
    df_hist = pd.DataFrame(res_hist.data or [])
    if df_hist.empty:
        print("❌ No hay partidos históricos para entrenar.")
        return

    df_train, elo_actual, historial_eq = construir_estadisticas_equipos(df_hist)
    print(f"   ✅ Generados {len(df_train)} vectores de entrenamiento con variables EWMA, Elo y Altitud.")

    # 4. Entrenar XGBoost y LightGBM
    features_cols = [
        "elo_diff", "h_xg_f", "h_xg_c", "a_xg_f", "a_xg_c",
        "h_tiros", "a_tiros", "h_corners", "a_corners",
        "h_tarjetas", "a_tarjetas", "descanso_diff", "altitud_diff"
    ]
    X = df_train[features_cols]
    y = df_train["target"]

    print("🤖 3. Entrenando modelos XGBoost y LightGBM en tiempo real...")
    xgb_model = XGBClassifier(
        n_estimators=140, max_depth=4, learning_rate=0.04,
        subsample=0.85, colsample_bytree=0.85, eval_metric="mlogloss", random_state=42
    )
    lgb_model = LGBMClassifier(
        n_estimators=140, max_depth=4, learning_rate=0.04,
        subsample=0.85, colsample_bytree=0.85, verbose=-1, random_state=42
    )
    xgb_model.fit(X, y)
    lgb_model.fit(X, y)

    # 5. Cargar pesos aprendidos por liga
    res_mem = supabase.table("memoria_modelos").select("*").execute()
    memoria_ligas = {m["liga_codigo"]: m for m in (res_mem.data or [])}

    # 6. Pronosticar los próximos partidos programados
    res_prog = supabase.table("partidos").select("*").eq("estado", "PROGRAMADO").order("fecha_utc").limit(35).execute()
    proximos = res_prog.data or []
    if not proximos:
        print("ℹ️ No hay próximos partidos programados en la base de datos.")
        return

    print(f"🔮 4. Calculando pronósticos para los próximos {len(proximos)} partidos...")
    lote_pronosticos = []
    mejores_alertas = []

    for p in proximos:
        pid = p["partido_id"]
        liga = p["liga_codigo"]
        h_id, a_id = p["local_id"], p["visita_id"]
        h_nom, a_nom = p["local_nombre"], p["visita_nombre"]
        fecha_p = pd.to_datetime(p["fecha_utc"])

        elo_h = elo_actual.get(h_id, 1500.0)
        elo_a = elo_actual.get(a_id, 1500.0)

        # Extraer racha EWMA de cada equipo
        def estado_actual_eq(eq_id):
            lista = historial_eq.get(eq_id, [])
            if not lista:
                return {"xg_f": 1.35, "xg_c": 1.25, "tiros": 11.0, "corners": 4.8, "tarjetas": 2.2, "ult_fecha": None}
            ult = lista[-6:]
            pesos = np.exp(np.linspace(-1.0, 0.0, len(ult)))
            pesos /= pesos.sum()
            return {
                "xg_f": float(np.sum([x["xg_f"] * w for x, w in zip(ult, pesos)])),
                "xg_c": float(np.sum([x["xg_c"] * w for x, w in zip(ult, pesos)])),
                "tiros": float(np.sum([x["tiros"] * w for x, w in zip(ult, pesos)])),
                "corners": float(np.sum([x["corners"] * w for x, w in zip(ult, pesos)])),
                "tarjetas": float(np.sum([x["tarjetas"] * w for x, w in zip(ult, pesos)])),
                "ult_fecha": ult[-1]["fecha"]
            }

        st_h = estado_actual_eq(h_id)
        st_a = estado_actual_eq(a_id)

        desc_h = min(240.0, max(48.0, (fecha_p - st_h["ult_fecha"]).total_seconds() / 3600.0)) if st_h["ult_fecha"] else 144.0
        desc_a = min(240.0, max(48.0, (fecha_p - st_a["ult_fecha"]).total_seconds() / 3600.0)) if st_a["ult_fecha"] else 144.0
        alt_dif = float(p.get("diferencial_altitud") or 0)
        lluvia = float(p.get("lluvia_mm") or 0.0)
        pot_h = float(p.get("potencia_11_local") or 1.0)
        pot_a = float(p.get("potencia_11_visita") or 1.0)

        # A) Tasas esperadas de goles (Lambda) con ajuste por localía, altitud, lluvia y alineación
        factor_altura_local = 1.14 if alt_dif >= 1800 else (0.92 if alt_dif <= -1800 else 1.0)
        factor_clima = 0.91 if lluvia >= 4.0 else 1.0

        lambda_h = max(0.35, ((st_h["xg_f"] * 0.58) + (st_a["xg_c"] * 0.42)) * 1.10 * factor_altura_local * factor_clima * pot_h)
        lambda_a = max(0.25, ((st_a["xg_f"] * 0.55) + (st_h["xg_c"] * 0.45)) * (1.0 / factor_altura_local) * factor_clima * pot_a)

        dc_1, dc_x, dc_2, prob_o25, prob_btts, marcador_top, _ = predecir_dixon_coles(lambda_h, lambda_a)

        # B) Predicción del Ensemble de Machine Learning (XGBoost + LightGBM)
        X_partido = pd.DataFrame([{
            "elo_diff": elo_h - elo_a,
            "h_xg_f": st_h["xg_f"], "h_xg_c": st_h["xg_c"],
            "a_xg_f": st_a["xg_f"], "a_xg_c": st_a["xg_c"],
            "h_tiros": st_h["tiros"], "a_tiros": st_a["tiros"],
            "h_corners": st_h["corners"], "a_corners": st_a["corners"],
            "h_tarjetas": st_h["tarjetas"], "a_tarjetas": st_a["tarjetas"],
            "descanso_diff": desc_h - desc_a,
            "altitud_diff": alt_dif
        }])
        probs_xgb = xgb_model.predict_proba(X_partido)[0]
        probs_lgb = lgb_model.predict_proba(X_partido)[0]
        ml_1 = float((probs_xgb[0] + probs_lgb[0]) / 2.0)
        ml_x = float((probs_xgb[1] + probs_lgb[1]) / 2.0)
        ml_2 = float((probs_xgb[2] + probs_lgb[2]) / 2.0)

        # C) Meta-Modelo: Fusión ponderada según memoria de auto-aprendizaje de esa liga
        mem = memoria_ligas.get(liga, {"peso_dixon_coles": 0.45, "peso_ml_xgboost": 0.55})
        w_dc = float(mem["peso_dixon_coles"])
        w_ml = float(mem["peso_ml_xgboost"])

        p1 = (dc_1 * w_dc) + (ml_1 * w_ml)
        px = (dc_x * w_dc) + (ml_x * w_ml)
        p2 = (dc_2 * w_dc) + (ml_2 * w_ml)
        suma = p1 + px + p2
        p1, px, p2 = p1 / suma, px / suma, p2 / suma

        # D) Mercados de Córners y Tarjetas proyectados
        corners_proy = round(st_h["corners"] + st_a["corners"] + (0.6 if lambda_h + lambda_a > 2.8 else 0.0), 1)
        tarjetas_proy = round(st_h["tarjetas"] + st_a["tarjetas"] + (0.5 if liga in ["ECU_LP", "ARG_LP", "SAM_CL", "SAM_CS"] else 0.0), 1)

        # Selección de la recomendación más sólida del partido (Sin Hándicap)
        candidatos = [
            (f"Victoria {h_nom} (1)", p1),
            (f"Victoria {a_nom} (2)", p2),
            ("Más de 2.5 Goles (Over 2.5)", prob_o25),
            ("Menos de 2.5 Goles (Under 2.5)", 1.0 - prob_o25),
            ("Ambos Equipos Anotan (Sí)", prob_btts),
            (f"Más de 8.5 Córners (Proy: {corners_proy})", min(0.85, max(0.20, (corners_proy - 6.5) / 5.5)))
        ]
        mejor_mercado, mejor_prob = max(candidatos, key=lambda item: item[1])
        # Estimación de Valor Esperado (EV%) frente a línea estándar del mercado
        ev_estimado = round((mejor_prob - 0.52) * 100, 1)

        registro = {
            "partido_id": pid,
            "fase_prediccion": "PREVIA_48H",
            "prob_1": round(p1, 4),
            "prob_x": round(px, 4),
            "prob_2": round(p2, 4),
            "xg_proyectado_local": round(lambda_h, 2),
            "xg_proyectado_visita": round(lambda_a, 2),
            "prob_over25": round(prob_o25, 4),
            "prob_btts": round(prob_btts, 4),
            "corners_proyectados": corners_proy,
            "tarjetas_proyectadas": tarjetas_proy,
            "dixon_coles_1": round(dc_1, 4),
            "dixon_coles_x": round(dc_x, 4),
            "dixon_coles_2": round(dc_2, 4),
            "ml_ensemble_1": round(ml_1, 4),
            "ml_ensemble_x": round(ml_x, 4),
            "ml_ensemble_2": round(ml_2, 4),
            "mercado_recomendado": mejor_mercado,
            "valor_esperado_ev": ev_estimado,
            "evaluado": False
        }
        lote_pronosticos.append(registro)

        mejores_alertas.append({
            "liga": LIGAS.get(liga, {}).get("nombre", liga),
            "partido": f"{h_nom} vs {a_nom}",
            "fecha": fecha_p.strftime("%d/%m %H:%M UTC"),
            "p1": round(p1 * 100, 1), "px": round(px * 100, 1), "p2": round(p2 * 100, 1),
            "xg": f"{round(lambda_h, 2)} - {round(lambda_a, 2)}",
            "o25": round(prob_o25 * 100, 1),
            "btts": round(prob_btts * 100, 1),
            "corners": corners_proy,
            "tarjetas": tarjetas_proy,
            "marcador": marcador_top,
            "pick": mejor_mercado,
            "confianza": round(mejor_prob * 100, 1),
            "altitud": int(alt_dif)
        })

    if lote_pronosticos:
        supabase.table("pronosticos_bot").upsert(lote_pronosticos).execute()
        print(f"   ✅ ¡{len(lote_pronosticos)} pronósticos guardados en Supabase!")

    # Ordenar por mayor nivel de confianza para enviar el Top 5 al celular por Telegram
    mejores_alertas.sort(key=lambda x: x["confianza"], reverse=True)
    top_5 = mejores_alertas[:5]

    bloques_txt = []
    for idx, item in enumerate(top_5, 1):
        nota_alt = f" | 🏔️ Altitud: {item['altitud']}m" if abs(item["altitud"]) >= 1500 else ""
        bloques_txt.append(
            f"*{idx}. {item['partido']}* ({item['liga']})\n"
            f"📅 `{item['fecha']}`{nota_alt}\n"
            f"📊 *1X2:* `{item['p1']}%` | `{item['px']}%` | `{item['p2']}%`\n"
            f"⚽ *xG Proy:* `{item['xg']}` | *Marcador Top:* `{item['marcador']}`\n"
            f"🔥 *Over 2.5:* `{item['o25']}%` | *BTTS:* `{item['btts']}%`\n"
            f"🚩 *Córners:* `{item['corners']}` | 🟨 *Tarjetas:* `{item['tarjetas']}`\n"
            f"💎 *Pick IA:* _{item['pick']}_ (*{item['confianza']}%*)"
        )

    mensaje = (
        "🧠 *REPORTE DEL ORÁCULO IA (CONSENSO DIXON-COLES + ML)*\n"
        f"📈 _Entrenado con {len(df_hist)} partidos históricos en tus 12 ligas_\n\n"
        + "\n\n".join(bloques_txt) +
        "\n\n🌐 _Todos los demás partidos ya están listos en la base de datos para tu Panel Web._"
    )

    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    )
    print("\n📱 ¡Alerta con el Top 5 de pronósticos enviada a tu Telegram!")

if __name__ == "__main__":
    ejecutar_cerebro_completo()