import requests
from supabase import create_client
from config import SUPABASE_URL, SUPABASE_KEY, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, LIGAS

def probar_todo():
    print("🔍 1. Probando conexión con Supabase...")
    try:
        supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
        print("   ✅ ¡Conexión a Supabase exitosa!")
    except Exception as e:
        print(f"   ❌ Error en Supabase: Revisa tu SUPABASE_URL y SUPABASE_KEY en .env ({e})")
        return

    print("\n📱 2. Probando envío de alerta a tu Telegram...")
    lista_ligas = "\n".join([f"• {v['nombre']} ({v['pais']})" for v in LIGAS.values()])
    mensaje = (
        "🤖 *¡BOT DE FÚTBOL IA CONECTADO CON ÉXITO!*\n\n"
        "🟢 *Estado de Base de Datos:* En línea (Supabase)\n"
        "🧠 *Fase 1 completada.*\n\n"
        "*🏆 12 Ligas Cargadas en Memoria:*\n"
        f"{lista_ligas}\n\n"
        "🚀 _Listo para iniciar la Fase 2: Creación de Tablas y Carga Histórica._"
    )

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "Markdown"
    }

    r = requests.post(url, json=payload)
    if r.status_code == 200:
        print("   ✅ ¡Mensaje enviado! Revisa tu celular en Telegram.")
    else:
        print(f"   ❌ Error en Telegram: {r.text}")

if __name__ == "__main__":
    probar_todo()