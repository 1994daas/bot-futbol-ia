from supabase import create_client
import os
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Limpiar los goles de Flamengo para que el script los recalcule con la regla de penales
supabase.table("partidos").update({
    "goles_local": None,
    "goles_visita": None,
    "estado": "PROGRAMADO"
}).ilike("local_nombre", "%Flamengo%").execute()

print("✅ Partido de Flamengo desmarcado. ¡Ya puedes correr el cerebro de nuevo!")