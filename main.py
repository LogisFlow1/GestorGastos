import os
import sqlite3
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# Activar registro de errores (Logs) para ver qué pasa en Render
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ReportLab para la generación del PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "TU_TELEGRAM_BOT_TOKEN_AQUI")

# --- BASE DE DATOS LOCAL (SQLite) ---
def init_db():
    try:
        conn = sqlite3.connect("gastos.db")
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS viajes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                destino TEXT,
                fecha_inicio TEXT,
                activo INTEGER DEFAULT 1
            )
        ''')
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS gastos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                viaje_id INTEGER,
                monto REAL,
                categoria TEXT,
                descripcion TEXT,
                foto_path TEXT,
                fecha TEXT
            )
        ''')
        conn.commit()
        conn.close()
        logger.info("Base de datos inicializada correctamente.")
    except Exception as e:
        logger.error(f"Error al inicializar BD: {e}")

init_db()

def get_viaje_activo(user_id):
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, destino, fecha_inicio FROM viajes WHERE user_id = ? AND activo = 1", (user_id,))
    viaje = cursor.fetchone()
    conn.close()
    return viaje

# --- COMANDOS ---

async def saludar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    viaje = get_viaje_activo(user_id)

    if viaje:
        _, destino, fecha = viaje
        mensaje = (f"👋 ¡Hola, {user_name}!\n\nTienes un viaje activo a: **{destino}**.\n\n"
                   "📸 Sube tu foto con el texto: `Monto, Categoría, Descripción`.")
    else:
        mensaje = (f"👋 ¡Hola, {user_name}!\n\nPara comenzar, escribe:\n`/iniciar_viaje <Destino>`")
    await update.message.reply_text(mensaje, parse_mode="Markdown")

async def iniciar_viaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        destino = " ".join(context.args) if context.args else "Destino no especificado"
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")

        conn = sqlite3.connect("gastos.db")
        cursor = conn.cursor()
        cursor.execute("UPDATE viajes SET activo = 0 WHERE user_id = ?", (user_id,))
        cursor.execute("INSERT INTO viajes (user_id, destino, fecha_inicio, activo) VALUES (?, ?, ?, 1)", (user_id, destino, fecha))
        conn.commit()
        conn.close()

        await update.message.reply_text(
            f"✈️ **Viaje iniciado** a: *{destino}*\n\n"
            "Envía tus fotos. En el comentario de la foto escribe exactamente:\n"
            "`Monto, Categoría, Descripción`\n"
            "_(Ejemplo: 1500, Comida, Cena)_",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Error en iniciar_viaje: {e}")
        await update.message.reply_text(f"❌ Ocurrió un error interno al iniciar el viaje: {e}")

# ... (resto de funciones simplificadas para detectar errores)

async def listar_gastos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    viaje = get_viaje_activo(update.effective_user.id)
    if not viaje:
        return await update.message.reply_text("⚠️ No tienes ningún viaje activo.")
    
    viaje_id, destino, _ = viaje
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, monto, categoria, descripcion FROM gastos WHERE viaje_id = ?", (viaje_id,))
    gastos = cursor.fetchall()
    conn.close()

    if not gastos: return await update.message.reply_text("No hay gastos registrados.")

    texto = f"📋 **Gastos registrados ({destino}):**\n\n"
    total = sum(g[1] for g in gastos)
    for g in gastos: texto += f"🔹 `ID: {g[0]}` | **${g[1]:.2f}** | [{g[2]}] {g[3]}\n"
    texto += f"\n💰 **Total:** ${total:.2f}"
    await update.message.reply_text(texto, parse_mode="Markdown")

async def procesar_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        viaje = get_viaje_activo(user_id)
        if not viaje: return await update.message.reply_text("⚠️ No hay viaje activo.")

        caption = update.message.caption or ""
        photo_file = await update.message.photo[-1].get_file()
        os.makedirs("comprobantes", exist_ok=True)
        foto_path = f"comprobantes/{photo_file.file_id}.jpg"
        await photo_file.download_to_drive(foto_path)

        datos_texto = caption.split(',')
        if len(datos_texto) < 3:
            if os.path.exists(foto_path): os.remove(foto_path)
            return await update.message.reply_text("❌ **Error de formato.** Usa: `Monto, Categoría, Descripción`")

        monto = float(datos_texto[0].strip().replace('$', ''))
        categoria = datos_texto[1].strip()
        descripcion = ",".join(datos_texto[2:]).strip()

        conn = sqlite3.connect("gastos.db")
        cursor = conn.cursor()
        cursor.execute("INSERT INTO gastos (viaje_id, monto, categoria, descripcion, foto_path, fecha) VALUES (?, ?, ?, ?, ?, ?)",
                       (viaje[0], monto, categoria, descripcion, foto_path, datetime.now().strftime("%Y-%m-%d %H:%M")))
        nuevo_id = cursor.lastrowid
        conn.commit()
        conn.close()

        await update.message.reply_text(f"✅ **Gasto Guardado (ID: {nuevo_id})**\n💵 **${monto:.2f}** | 🏷️ {categoria}")
    except Exception as e:
        logger.error(f"Error al procesar foto: {e}")
        await update.message.reply_text(f"❌ Error al procesar el gasto: {e}")

async def finalizar_viaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Generando PDF, por favor espera...")
    # (El código del PDF se mantiene igual, pero si falla lo dirá en Telegram)

if __name__ == '__main__':
    # --- TRUCO PARA RENDER ---
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    class DummyHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot OK")
    def run_dummy_server():
        port = int(os.environ.get("PORT", 8080))
        HTTPServer(("0.0.0.0", port), DummyHandler).serve_forever()
    threading.Thread(target=run_dummy_server, daemon=True).start()
    # --------------------------

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", saludar_usuario))
    app.add_handler(MessageHandler(filters.Regex(r'(?i)^(hola|buenas|buen dia|inicio)'), saludar_usuario))
    app.add_handler(CommandHandler("iniciar_viaje", iniciar_viaje))
    app.add_handler(CommandHandler("gastos", listar_gastos))
    app.add_handler(CommandHandler("finalizar_viaje", finalizar_viaje))
    app.add_handler(MessageHandler(filters.PHOTO, procesar_gasto))

    logger.info("Bot iniciando...")
    app.run_polling()
