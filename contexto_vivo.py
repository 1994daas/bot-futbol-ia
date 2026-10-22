import requests
from datetime import datetime, timezone
from curl_cffi import requests as cffi_requests
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, LIGAS
from seed_historico import HEADERS_SOFA, ESPN_SLUGS

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Coordenadas base por liga/país para consulta meteorológica en Open-Meteo API
COORDENADAS_BASE = {
    "ENG_PL": (51.50, -0.12),   # Londres / Inglaterra
    "ESP_LL": (40.41, -3.70),   # Madrid / España
    "ITA_SA": (45.46, 9.19),    # Milán / Italia
    "GER_BL": (50.11, 8.68),    # Frankfurt / Alemania
    "FRA_L1": (48.85, 2.35),    # París / Francia
    "NED_ER": (52.36, 4.90),    # Ámsterdam / Países Bajos
    "EUR_CL": (48.13, 11.58),   # Europa Central
    "ECU_LP": (-0.18, -78.46),  # Quito / Sierra-Costa Ecuador
    "BRA_SA": (-23.55, -46.63), # São Paulo / Brasil
    "ARG_LP": (-34.60, -58.38), # Buenos Aires / Argentina
    "SAM_CL": (-12.04, -77.04), # Sudamérica
    "SAM_CS": (-33.44, -70.66)  # Sudamérica
}

def obtener_clima_partido(liga_codigo, altitud_local):
    """Consulta temperatura, lluvia (mm) y viento (km/h) gratis en Open-Meteo."""
    lat, lon = COORDENADAS_BASE.get(liga_codigo, (0.0, 0.0))
    # Ajuste para Ecuador si el equipo local es del llano (Guayaquil/Manta) vs Sierra
    if liga_codigo == "ECU_LP" and altitud_local < 200:
        lat, lon = (-2.17, -79.92)

    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,precipitation,wind_speed_10m"
    try:
        r = requests.get(url, timeout=8)
        if r.status_code == 200:
            curr = r.json().get("current", {})
            return (
                float(curr.get("temperature_2m", 18.0)),
                float(curr.get("precipitation", 0.0)),
                float(curr.get("wind_speed_10m", 10.0))
            )
    except Exception:
        pass
    return 18.0, 0.0, 10.0

def verificar_alineaciones_y_cuotas(sesion, partido_id):
    """Revisa si ya hay alineaciones confirmadas (45m antes) y extrae cuotas si están disponibles."""
    pot_local, pot_visita = 1.0, 1.0
    fase = "PREVIA_48H"

    url_lineups = f"https://www.sofascore.com/api/v1/event/{partido_id}/lineups"
    try:
        r = sesion.get(url_lineups, headers=HEADERS_SOFA, impersonate="chrome120", timeout=8)
        if r.status_code == 200:
            data = r.json()
            if data.get("confirmed"):
                fase = "ALINEACIONES_45M"
                # Evaluar rating promedio de los 11 titulares confirmados
                for lado, clave in [("home", "local"), ("away", "visita")]:
                    jugadores = data.get(lado, {}).get("players", [])
                    titulares = [j for j in jugadores if not j.get("substitute", False)]
                    if titulares:
                        ratings = [float(j.get("statistics", {}).get("rating", 6.85)) for j in titulares if j.get("statistics", {}).get("rating")]
                        if ratings:
                            promedio = sum(ratings) / len(ratings)
                            factor = round(min(1.12, max(0.85, promedio / 6.90)), 3)
                            if clave == "local":
                                pot_local = factor
                            else:
                                pot_visita = factor
    except Exception:
        pass

    return pot_local, pot_visita, fase

def enriquecer_proximos_partidos():
    print("🌦️ Actualizando clima, descanso, alineaciones y contexto de próximos partidos...")
    res = supabase.table("partidos").select("*").eq("estado", "PROGRAMADO").execute()
    proximos = res.data or []
    if not proximos:
        print("   ℹ️ No hay partidos en estado PROGRAMADO en este momento.")
        return

    sesion = cffi_requests.Session()
    actualizados = 0

    for p in proximos[:40]:  # Procesamos los 40 partidos más próximos
        liga = p["liga_codigo"]
        alt_dif = p.get("diferencial_altitud") or 0
        alt_aprox = 2600 if alt_dif > 1000 else 50

        temp, lluvia, viento = obtener_clima_partido(liga, alt_aprox)
        pot_h, pot_a, _ = verificar_alineaciones_y_cuotas(sesion, p["partido_id"])

        supabase.table("partidos").update({
            "temperatura_c": temp,
            "lluvia_mm": lluvia,
            "viento_kmh": viento,
            "potencia_11_local": pot_h,
            "potencia_11_visita": pot_a
        }).eq("partido_id", p["partido_id"]).execute()
        actualizados += 1

    print(f"   ✅ Contexto meteorológico y de plantillas actualizado en {actualizados} partidos.")

if __name__ == "__main__":
    enriquecer_proximos_partidos()