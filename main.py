import os
import sys
import re
import logging
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
from weasyprint import HTML

# Configuración de Logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- SERVIDOR HTTP DUMMY PARA RENDER ---
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot de Gastos Activo")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

threading.Thread(target=run_dummy_server, daemon=True).start()

# --- FUNCION EXTRAER MONTO CON API OCR (Ultraligera) ---
def extraer_monto_ocr(ruta_imagen):
    try:
        # Usa la API gratuita de OCR.Space
        url = 'https://api.ocr.space/parse/image'
        with open(ruta_imagen, 'rb') as f:
            response = requests.post(
                url,
                files={'filename': f},
                data={'apikey': 'helloworld', 'language': 'spa', 'isOverlayRequired': False}
            )
        
        result = response.json()
        if not result.get('ParsedResults'):
            return 0.0

        texto_unido = result['ParsedResults'][0]['ParsedText']
        print(f">>> TEXTO DETECTADO POR OCR: {texto_unido}", flush=True)

        lineas = texto_unido.split('\r\n')

        # 1. Palabras clave
        for linea in lineas:
            if any(palabra in linea.lower() for palabra in ['total', 'importe', 'monto', 'pagar', 'suma', '$']):
                numeros = re.findall(r'\d+(?:[.,]\d+)?', linea)
                if numeros:
                    return float(numeros[-1].replace(',', '.'))

        # 2. Patrón de decimales
        montos = re.findall(r'\b\d+[.,]\d{2}\b', texto_unido)
        if montos:
            return float(montos[-1].replace(',', '.'))

        # 3. Número más alto
        todos_los_numeros = re.findall(r'\b\d+(?:[.,]\d+)?\b', texto_unido)
        if todos_los_numeros:
            numeros_limpios = [float(n.replace(',', '.')) for n in todos_los_numeros if len(n) > 1]
            if numeros_limpios:
                return max(numeros_limpios)

    except Exception as e:
        print(f"❌ Error en llamada OCR: {e}", flush=True)

    return 0.0

# --- HANDLERS DEL BOT DE TELEGRAM ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['gastos'] = []
    await update.message.reply_text(
        "🚀 **¡Nuevo viaje/rendición iniciada!**\n\nEnvíame la foto de tu primer ticket o factura para registrar el gasto."
    )

async def procesar_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analizando comprobante con OCR...")
    
    foto = await update.message.photo[-1].get_file()
    ruta_local = "ticket_temp.jpg"
    await foto.download_to_drive(ruta_local)

    monto_detectado = extraer_monto_ocr(ruta_local)

    if os.path.exists(ruta_local):
        os.remove(ruta_local)

    if 'gastos' not in context.user_data:
        context.user_data['gastos'] = []
    
    context.user_data['gastos'].append(monto_detectado)
    total_acumulado = sum(context.user_data['gastos'])
    
    keyboard = [
        [InlineKeyboardButton("➕ Cargar otro gasto", callback_data='cargar_otro')],
        [InlineKeyboardButton("📄 Cerrar viaje y generar PDF", callback_data='cerrar_viaje')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    mensaje_respuesta = (
        f"✅ **Gasto detectado:** ${monto_detectado:.2f}\n"
        f"💰 **Total acumulado en este viaje:** `${total_acumulado:.2f}`\n\n"
        "¿Qué deseas hacer ahora?"
    )

    await update.message.reply_text(mensaje_respuesta, reply_markup=reply_markup, parse_mode='Markdown')

async def manejar_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'cargar_otro':
        total_actual = sum(context.user_data.get('gastos', []))
        await query.edit_message_text(
            f"📸 **Listo para el siguiente gasto.**\n"
            f"Llevas acumulado: `${total_actual:.2f}`\n\n"
            "Envíame la foto del siguiente ticket.",
            parse_mode='Markdown'
        )

    elif query.data == 'cerrar_viaje':
        gastos = context.user_data.get('gastos', [])
        total = sum(gastos)
        
        await query.edit_message_text("⏳ Generando el reporte PDF del viaje...")

        items_html = "".join([f"<li>Gasto #{i+1}: ${g:.2f}</li>" for i, g in enumerate(gastos)])
        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; padding: 30px; }}
                h1 {{ color: #1a237e; }}
                ul {{ font-size: 16px; line-height: 1.6; }}
                .total {{ font-size: 20px; font-weight: bold; margin-top: 20px; color: #2e7d32; }}
            </style>
        </head>
        <body>
            <h1>Reporte de Gastos de Viaje</h1>
            <hr>
            <h3>Detalle de Comprobantes:</h3>
            <ul>{items_html}</ul>
            <div class="total">Total Rendido: ${total:.2f}</div>
        </body>
        </html>
        """
        
        pdf_path = "reporte_gastos.pdf"
        HTML(string=html_content).write_pdf(pdf_path)

        with open(pdf_path, 'rb') as pdf_file:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=pdf_file,
                filename="Reporte_Gastos_Viaje.pdf",
                caption=f"📄 **Viaje Cerrado**\nTotal procesado: **${total:.2f}**\n\nPara iniciar otra rendición, envía `/start`.",
                parse_mode='Markdown'
            )

        if os.path.exists(pdf_path):
            os.remove(pdf_path)

        context.user_data['gastos'] = []

# --- INICIALIZACIÓN DE LA APLICACIÓN ---
if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        print("❌ ERROR: No se encontró TELEGRAM_TOKEN", flush=True)
        sys.exit(1)

    app = ApplicationBuilder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.PHOTO, procesar_imagen))
    app.add_handler(CallbackQueryHandler(manejar_botones))

    print(">>> BOT EN MARCHA Y ESCUCHANDO MENSAJES <<<", flush=True)
    app.run_polling()
