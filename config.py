import os
from dotenv import load_dotenv

# Cargar variables secretas del archivo .env
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# ==========================================================
# LISTA OFICIAL DE LAS 12 LIGAS Y TORNEOS DEL BOT
# ==========================================================
LIGAS = {
    # Bloque Europeo + Mina de Oro (7)
    "ENG_PL": {"nombre": "Premier League", "pais": "Inglaterra", "sofascore_id": 17, "factor_altura": False},
    "ESP_LL": {"nombre": "LaLiga", "pais": "España", "sofascore_id": 8, "factor_altura": False},
    "ITA_SA": {"nombre": "Serie A", "pais": "Italia", "sofascore_id": 23, "factor_altura": False},
    "GER_BL": {"nombre": "Bundesliga", "pais": "Alemania", "sofascore_id": 35, "factor_altura": False},
    "FRA_L1": {"nombre": "Ligue 1", "pais": "Francia", "sofascore_id": 34, "factor_altura": False},
    "NED_ER": {"nombre": "Eredivisie", "pais": "Países Bajos", "sofascore_id": 37, "factor_altura": False},
    "EUR_CL": {"nombre": "UEFA Champions League", "pais": "Europa", "sofascore_id": 7, "factor_altura": False},

    # Bloque Sudamericano (5) - Con ajuste de Altitud y Viajes Largos
    "ECU_LP": {"nombre": "LigaPro Serie A", "pais": "Ecuador", "sofascore_id": 240, "factor_altura": True},
    "BRA_SA": {"nombre": "Brasileirão Serie A", "pais": "Brasil", "sofascore_id": 325, "factor_altura": False},
    "ARG_LP": {"nombre": "Liga Profesional", "pais": "Argentina", "sofascore_id": 155, "factor_altura": False},
    "SAM_CL": {"nombre": "Copa Libertadores", "pais": "Sudamérica", "sofascore_id": 384, "factor_altura": True},
    "SAM_CS": {"nombre": "Copa Sudamericana", "pais": "Sudamérica", "sofascore_id": 480, "factor_altura": True},
}

# Mercados activos del Bot (Sin Hándicap)
MERCADOS_ACTIVOS = [
    "1X2",          # Ganador del partido (Local, Empate, Visita)
    "OVER_UNDER",   # Más / Menos de 2.5 Goles (y líneas 1.5 / 3.5)
    "BTTS",         # Ambos Equipos Anotan (Sí / No)
    "CORNERS",      # Más / Menos Córners proyectados
    "TARJETAS"      # Más / Menos Tarjetas proyectadas según árbitro y equipos
]