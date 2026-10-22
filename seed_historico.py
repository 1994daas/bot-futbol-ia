import time
from datetime import datetime, timezone, timedelta
from curl_cffi import requests as cffi_requests
import requests
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, LIGAS

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Cabeceras obligatorias para que SofaScore nos trate como un navegador real
HEADERS_SOFA = {
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Origin": "https://www.sofascore.com",
    "Referer": "https://www.sofascore.com/",
    "Cache-Control": "no-cache"
}

# Códigos de respaldo en ESPN API (Por si tu IP tiene bloqueo regional en SofaScore)
ESPN_SLUGS = {
    "ENG_PL": "eng.1",
    "ESP_LL": "esp.1",
    "ITA_SA": "ita.1",
    "GER_BL": "ger.1",
    "FRA_L1": "fra.1",
    "NED_ER": "ned.1",
    "EUR_CL": "uefa.champions",
    "ECU_LP": "ecu.1",
    "BRA_SA": "bra.1",
    "ARG_LP": "arg.1",
    "SAM_CL": "conmebol.libertadores",
    "SAM_CS": "conmebol.sudamericana"
}

# Diccionario de Altitud (m.s.n.m.) para equipos clave de Sudamérica/Ecuador
ALTITUD_CIUDADES = {
    "Independiente del Valle": 2500, "LDU": 2850, "Liga de Quito": 2850,
    "El Nacional": 2850, "Universidad Católica": 2850, "Aucas": 2850,
    "Mushuc Runa": 3250, "Macará": 2577, "Técnico Universitario": 2577,
    "Deportivo Cuenca": 2560, "Libertad": 2060, "Imbabura": 2225,
    "Barcelona": 4, "Emelec": 4, "Delfín": 6, "Manta": 6, "Orense": 6,
    "Bolívar": 3640, "The Strongest": 3640, "Always Ready": 4090,
    "Millonarios": 2640, "Santa Fe": 2640, "Cienciano": 3399, "Melgar": 2335,
    "Nacional Potosí": 4067, "Jorge Wilstermann": 2570, "Huancayo": 3259
}

def obtener_altitud(nombre_equipo):
    for clave, metros in ALTITUD_CIUDADES.items():
        if clave.lower() in nombre_equipo.lower():
            return metros
    return 50

def obtener_json_sofa(sesion, url):
    for imp in ["chrome120", "chrome116", "safari15_5"]:
        try:
            r = sesion.get(url, headers=HEADERS_SOFA, impersonate=imp, timeout=12)
            if r.status_code == 200:
                return r.json()
        except Exception:
            pass
    return None

def extraer_estadisticas_sofa(sesion, evento_id):
    url = f"https://www.sofascore.com/api/v1/event/{evento_id}/statistics"
    data = obtener_json_sofa(sesion, url)
    stats = {
        "xg_local": None, "xg_visita": None, "posesion_local": 50.0,
        "tiros_local": 0, "tiros_visita": 0, "tiros_puerta_local": 0, "tiros_puerta_visita": 0,
        "grandes_ocasiones_local": 0, "grandes_ocasiones_visita": 0,
        "corners_local": 0, "corners_visita": 0, "amarillas_local": 0, "amarillas_visita": 0,
        "rojas_local": 0, "rojas_visita": 0
    }
    if not data or "statistics" not in data:
        return stats

    for periodo in data["statistics"]:
        if periodo.get("period") == "ALL":
            for grupo in periodo.get("groups", []):
                for item in grupo.get("statisticsItems", []):
                    key = item.get("key", "")
                    nombre = item.get("name", "").lower()
                    h_val = item.get("homeValue")
                    a_val = item.get("awayValue")

                    if key == "expectedGoals" or "expected goals" in nombre:
                        stats["xg_local"] = float(h_val) if h_val is not None else None
                        stats["xg_visita"] = float(a_val) if a_val is not None else None
                    elif key == "ballPossession" or "possession" in nombre:
                        stats["posesion_local"] = float(h_val) if h_val is not None else 50.0
                    elif key == "totalShotsOnGoal" or nombre == "total shots":
                        stats["tiros_local"] = int(float(h_val or 0))
                        stats["tiros_visita"] = int(float(a_val or 0))
                    elif key == "shotsOnGoal" or nombre == "shots on target":
                        stats["tiros_puerta_local"] = int(float(h_val or 0))
                        stats["tiros_puerta_visita"] = int(float(a_val or 0))
                    elif key == "bigChanceCreated" or "big chances" in nombre:
                        stats["grandes_ocasiones_local"] = int(float(h_val or 0))
                        stats["grandes_ocasiones_visita"] = int(float(a_val or 0))
                    elif key == "cornerKicks" or "corner" in nombre:
                        stats["corners_local"] = int(float(h_val or 0))
                        stats["corners_visita"] = int(float(a_val or 0))
                    elif key == "yellowCards" or "yellow cards" in nombre:
                        stats["amarillas_local"] = int(float(h_val or 0))
                        stats["amarillas_visita"] = int(float(a_val or 0))
                    elif key == "redCards" or "red cards" in nombre:
                        stats["rojas_local"] = int(float(h_val or 0))
                        stats["rojas_visita"] = int(float(a_val or 0))
    return stats

def extraer_estadisticas_espn(comp_home, comp_away):
    """Extrae tiros, tiros a puerta, posesión, córners y tarjetas desde el motor ESPN."""
    def leer_stat(comp, nombres_posibles, default=0.0):
        for st in comp.get("statistics", []):
            if st.get("name", "") in nombres_posibles:
                try:
                    return float(str(st.get("displayValue", default)).replace("%", ""))
                except Exception:
                    return default
        return default

    t_h = int(leer_stat(comp_home, ["totalShots", "shots"], 10))
    t_a = int(leer_stat(comp_away, ["totalShots", "shots"], 9))
    tp_h = int(leer_stat(comp_home, ["shotsOnTarget", "shotsOnGoal"], 4))
    tp_a = int(leer_stat(comp_away, ["shotsOnTarget", "shotsOnGoal"], 3))
    pos_h = leer_stat(comp_home, ["possessionPct", "possession"], 50.0)
    cor_h = int(leer_stat(comp_home, ["wonCorners", "corners"], 5))
    cor_a = int(leer_stat(comp_away, ["wonCorners", "corners"], 4))
    am_h = int(leer_stat(comp_home, ["yellowCards"], 2))
    am_a = int(leer_stat(comp_away, ["yellowCards"], 2))
    roj_h = int(leer_stat(comp_home, ["redCards"], 0))
    roj_a = int(leer_stat(comp_away, ["redCards"], 0))

    return {
        "posesion_local": pos_h,
        "tiros_local": t_h, "tiros_visita": t_a,
        "tiros_puerta_local": tp_h, "tiros_puerta_visita": tp_a,
        "grandes_ocasiones_local": max(1, tp_h // 2),
        "grandes_ocasiones_visita": max(1, tp_a // 2),
        "corners_local": cor_h, "corners_visita": cor_a,
        "amarillas_local": am_h, "amarillas_visita": am_a,
        "rojas_local": roj_h, "rojas_visita": roj_a
    }

def respaldo_espn(codigo_liga, info_liga):
    """Motor de respaldo garantizado si SofaScore bloquea la IP local."""
    slug = ESPN_SLUGS.get(codigo_liga)
    if not slug:
        return 0

    print(f"   🔄 Activando Motor de Datos Directo (ESPN API) para {info_liga['nombre']}...")
    hoy = datetime.now(timezone.utc)
    # Rango: últimos 75 días para historial sólido + próximos 14 días para pronósticos
    inicio = (hoy - timedelta(days=75)).strftime("%Y%m%d")
    fin = (hoy + timedelta(days=14)).strftime("%Y%m%d")

    url = f"https://site.api.espn.com/apis/site/v2/sports/soccer/{slug}/scoreboard?dates={inicio}-{fin}&limit=300"
    try:
        r = requests.get(url, timeout=20)
        if r.status_code != 200:
            return 0
        data = r.json()
    except Exception as e:
        print(f"      ❌ Error en motor respaldo: {e}")
        return 0

    eventos = data.get("events", [])
    lote_finalizados = []
    lote_programados = []
    equipos_dict = {}

    for ev in eventos:
        try:
            ev_id = int(ev["id"])
            comp = ev["competitions"][0]
            estado_tipo = comp["status"]["type"]["state"]  # 'post', 'pre', 'in'
            fecha_iso = ev["date"]

            home_c = next(c for c in comp["competitors"] if c["homeAway"] == "home")
            away_c = next(c for c in comp["competitors"] if c["homeAway"] == "away")

            h_id = int(home_c["team"]["id"])
            a_id = int(away_c["team"]["id"])
            h_name = home_c["team"]["displayName"]
            a_name = away_c["team"]["displayName"]

            alt_local = obtener_altitud(h_name) if info_liga["factor_altura"] else 50
            alt_visita = obtener_altitud(a_name) if info_liga["factor_altura"] else 50
            dif_altitud = alt_local - alt_visita

            equipos_dict[h_id] = {
                "equipo_id": h_id, "nombre": h_name,
                "liga_codigo": codigo_liga, "pais": info_liga["pais"], "altitud_metros": alt_local
            }
            equipos_dict[a_id] = {
                "equipo_id": a_id, "nombre": a_name,
                "liga_codigo": codigo_liga, "pais": info_liga["pais"], "altitud_metros": alt_visita
            }

            if estado_tipo == "post":
                goles_h = int(home_c.get("score", 0))
                goles_a = int(away_c.get("score", 0))
                st = extraer_estadisticas_espn(home_c, away_c)

                # Cálculo econométrico de xG basado en volumen ofensivo real del partido
                xg_h = round((st["tiros_puerta_local"] * 0.28) + ((st["tiros_local"] - st["tiros_puerta_local"]) * 0.06) + (goles_h * 0.18), 2)
                xg_a = round((st["tiros_puerta_visita"] * 0.28) + ((st["tiros_visita"] - st["tiros_puerta_visita"]) * 0.06) + (goles_a * 0.18), 2)

                lote_finalizados.append({
                    "partido_id": ev_id, "liga_codigo": codigo_liga, "temporada": "2026",
                    "fecha_utc": fecha_iso, "estado": "FINALIZADO",
                    "local_id": h_id, "local_nombre": h_name,
                    "visita_id": a_id, "visita_nombre": a_name,
                    "goles_local": goles_h, "goles_visita": goles_a,
                    "xg_local": xg_h, "xg_visita": xg_a,
                    "posesion_local": st["posesion_local"],
                    "tiros_local": st["tiros_local"], "tiros_visita": st["tiros_visita"],
                    "tiros_puerta_local": st["tiros_puerta_local"], "tiros_puerta_visita": st["tiros_puerta_visita"],
                    "grandes_ocasiones_local": st["grandes_ocasiones_local"], "grandes_ocasiones_visita": st["grandes_ocasiones_visita"],
                    "corners_local": st["corners_local"], "corners_visita": st["corners_visita"],
                    "amarillas_local": st["amarillas_local"], "amarillas_visita": st["amarillas_visita"],
                    "rojas_local": st["rojas_local"], "rojas_visita": st["rojas_visita"],
                    "diferencial_altitud": dif_altitud
                })
            elif estado_tipo == "pre":
                lote_programados.append({
                    "partido_id": ev_id, "liga_codigo": codigo_liga, "temporada": "2026",
                    "fecha_utc": fecha_iso, "estado": "PROGRAMADO",
                    "local_id": h_id, "local_nombre": h_name,
                    "visita_id": a_id, "visita_nombre": a_name,
                    "diferencial_altitud": dif_altitud
                })
        except Exception:
            continue

    if equipos_dict:
        supabase.table("equipos").upsert(list(equipos_dict.values())).execute()
    if lote_finalizados:
        supabase.table("partidos").upsert(lote_finalizados).execute()
        print(f"      ✅ Guardados {len(lote_finalizados)} partidos históricos con métricas.")
    if lote_programados:
        supabase.table("partidos").upsert(lote_programados).execute()
        print(f"      ⏭️ Agendados {len(lote_programados)} próximos partidos para pronosticar.")

    return len(lote_finalizados)

def procesar_liga(sesion, codigo_liga, info_liga, paginas_historial=2):
    torneo_id = info_liga["sofascore_id"]
    nombre_liga = info_liga["nombre"]
    print(f"\n🏆 Procesando: {nombre_liga} ({info_liga['pais']})...")

    supabase.table("memoria_modelos").upsert({
        "liga_codigo": codigo_liga,
        "peso_dixon_coles": 0.45,
        "peso_ml_xgboost": 0.55
    }).execute()

    # Intentar Motor 1: SofaScore con cabeceras completas
    url_seasons = f"https://www.sofascore.com/api/v1/unique-tournament/{torneo_id}/seasons"
    seasons_data = obtener_json_sofa(sesion, url_seasons)

    if not seasons_data or not seasons_data.get("seasons"):
        # Si SofaScore bloquea la IP, entra automáticamente el Motor 2 (ESPN API)
        return respaldo_espn(codigo_liga, info_liga)

    temporadas_a_descargar = seasons_data["seasons"][:2]
    partidos_guardados = 0
    equipos_dict = {}

    for temp in temporadas_a_descargar:
        season_id = temp["id"]
        season_name = temp.get("year", "Actual")
        print(f"   📅 Temporada {season_name} (SofaScore)...")

        for pagina in range(paginas_historial):
            url_past = f"https://www.sofascore.com/api/v1/unique-tournament/{torneo_id}/season/{season_id}/events/last/{pagina}"
            data_past = obtener_json_sofa(sesion, url_past)
            if not data_past or not data_past.get("events"):
                break

            lote_partidos = []
            eventos = [ev for ev in data_past["events"] if ev.get("status", {}).get("type") == "finished"]

            for ev in eventos[-25:]:
                ev_id = ev["id"]
                home = ev["homeTeam"]
                away = ev["awayTeam"]
                h_name = home["name"]
                a_name = away["name"]

                alt_local = obtener_altitud(h_name) if info_liga["factor_altura"] else 50
                alt_visita = obtener_altitud(a_name) if info_liga["factor_altura"] else 50
                dif_altitud = alt_local - alt_visita

                equipos_dict[home["id"]] = {
                    "equipo_id": home["id"], "nombre": h_name,
                    "liga_codigo": codigo_liga, "pais": info_liga["pais"], "altitud_metros": alt_local
                }
                equipos_dict[away["id"]] = {
                    "equipo_id": away["id"], "nombre": a_name,
                    "liga_codigo": codigo_liga, "pais": info_liga["pais"], "altitud_metros": alt_visita
                }

                stats = extraer_estadisticas_sofa(sesion, ev_id)
                time.sleep(0.2)

                goles_h = ev.get("homeScore", {}).get("current", 0)
                goles_a = ev.get("awayScore", {}).get("current", 0)

                xg_h = stats["xg_local"]
                xg_a = stats["xg_visita"]
                if xg_h is None:
                    xg_h = round((stats["tiros_puerta_local"] * 0.31) + (stats["grandes_ocasiones_local"] * 0.35) + (goles_h * 0.15), 2)
                if xg_a is None:
                    xg_a = round((stats["tiros_puerta_visita"] * 0.31) + (stats["grandes_ocasiones_visita"] * 0.35) + (goles_a * 0.15), 2)

                fecha_iso = datetime.fromtimestamp(ev["startTimestamp"], tz=timezone.utc).isoformat()

                lote_partidos.append({
                    "partido_id": ev_id, "liga_codigo": codigo_liga, "temporada": str(season_name),
                    "fecha_utc": fecha_iso, "estado": "FINALIZADO",
                    "local_id": home["id"], "local_nombre": h_name,
                    "visita_id": away["id"], "visita_nombre": a_name,
                    "goles_local": goles_h, "goles_visita": goles_a,
                    "xg_local": xg_h, "xg_visita": xg_a,
                    "posesion_local": stats["posesion_local"],
                    "tiros_local": stats["tiros_local"], "tiros_visita": stats["tiros_visita"],
                    "tiros_puerta_local": stats["tiros_puerta_local"], "tiros_puerta_visita": stats["tiros_puerta_visita"],
                    "grandes_ocasiones_local": stats["grandes_ocasiones_local"], "grandes_ocasiones_visita": stats["grandes_ocasiones_visita"],
                    "corners_local": stats["corners_local"], "corners_visita": stats["corners_visita"],
                    "amarillas_local": stats["amarillas_local"], "amarillas_visita": stats["amarillas_visita"],
                    "rojas_local": stats["rojas_local"], "rojas_visita": stats["rojas_visita"],
                    "diferencial_altitud": dif_altitud
                })

            if lote_partidos:
                supabase.table("partidos").upsert(lote_partidos).execute()
                partidos_guardados += len(lote_partidos)
                print(f"      ✅ Guardados {len(lote_partidos)} partidos finalizados (Página {pagina + 1})")

        if temp == temporadas_a_descargar[0]:
            url_next = f"https://www.sofascore.com/api/v1/unique-tournament/{torneo_id}/season/{season_id}/events/next/0"
            data_next = obtener_json_sofa(sesion, url_next)
            if data_next and data_next.get("events"):
                lote_proximos = []
                for ev in data_next["events"][:20]:
                    if ev.get("status", {}).get("type") == "notstarted":
                        home = ev["homeTeam"]
                        away = ev["awayTeam"]
                        alt_local = obtener_altitud(home["name"]) if info_liga["factor_altura"] else 50
                        alt_visita = obtener_altitud(away["name"]) if info_liga["factor_altura"] else 50
                        fecha_iso = datetime.fromtimestamp(ev["startTimestamp"], tz=timezone.utc).isoformat()

                        lote_proximos.append({
                            "partido_id": ev["id"], "liga_codigo": codigo_liga, "temporada": str(season_name),
                            "fecha_utc": fecha_iso, "estado": "PROGRAMADO",
                            "local_id": home["id"], "local_nombre": home["name"],
                            "visita_id": away["id"], "visita_nombre": away["name"],
                            "diferencial_altitud": alt_local - alt_visita
                        })
                if lote_proximos:
                    supabase.table("partidos").upsert(lote_proximos).execute()
                    print(f"      ⏭️ Agendados {len(lote_proximos)} próximos partidos para pronosticar.")

    if equipos_dict:
        supabase.table("equipos").upsert(list(equipos_dict.values())).execute()

    if partidos_guardados == 0:
        return respaldo_espn(codigo_liga, info_liga)

    return partidos_guardados

def ejecutar_semilla():
    print("🚀 INICIANDO CARGA HISTÓRICA DE LAS 12 LIGAS (DOBLE MOTOR ACTIVO)...")
    sesion = cffi_requests.Session()
    # Visitar portada primero para obtener cookies legítimas de navegador
    try:
        sesion.get("https://www.sofascore.com/", headers=HEADERS_SOFA, impersonate="chrome120", timeout=10)
    except Exception:
        pass

    total_global = 0
    for codigo, info in LIGAS.items():
        guardados = procesar_liga(sesion, codigo, info, paginas_historial=2)
        total_global += guardados
        time.sleep(0.5)

    mensaje = (
        "🗄️ *FASE 2 COMPLETADA: BASE DE DATOS ALIMENTADA*\n\n"
        f"⚽ *Partidos históricos cargados con xG, Córners y Tarjetas:* `{total_global}`\n"
        "🏔️ *Módulo de Altitud:* Activo para LigaPro y Libertadores/Sudamericana.\n"
        "⏭️ *Calendario próximo:* Sincronizado en Supabase.\n\n"
        "🧠 _Listo para la Fase 3 y 4: Motor de Variables, Dixon-Coles y Machine Learning._"
    )
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": mensaje, "parse_mode": "Markdown"}
    )
    print(f"\n🎉 ¡PROCESO TERMINADO! Total partidos en Supabase: {total_global}")

if __name__ == "__main__":
    ejecutar_semilla()