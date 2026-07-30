import os
import sys
import re
import logging
import threading
import asyncio
import base64
import requests
from io import BytesIO
from PIL import Image
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    filters,
    ContextTypes
)
from weasyprint import HTML

# Configuración de Logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# --- ESTADOS DE LA CONVERSACIÓN ---
TITULO_VIAJE, ESPERANDO_FOTO, EDITANDO_MONTO = range(3)

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
    context.user_data['titulo_viaje'] = "Rendición de Gastos"
    
    await update.message.reply_text(
        "🚀 **¡Nuevo viaje iniciado!**\n\nPor favor, escribe el **título o motivo del viaje** (ejemplo: *Viaje de Negocios Córdoba*):"
    )
    return TITULO_VIAJE

async def recibir_titulo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['titulo_viaje'] = update.message.text
    
    await update.message.reply_text(
        f"📋 Viaje registrado como: **{context.user_data['titulo_viaje']}**\n\n"
        "Ahora, envíame la foto del **primer comprobante/ticket** para procesarlo."
    )
    return ESPERANDO_FOTO

async def procesar_imagen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Analizando comprobante con OCR...")
    
    foto = await update.message.photo[-1].get_file()
    ruta_local = f"ticket_temp_{update.message.chat_id}.jpg"
    await foto.download_to_drive(ruta_local)

    with Image.open(ruta_local) as img:
        img.thumbnail((800, 800))
        buffered = BytesIO()
        img.save(buffered, format="JPEG", quality=80)
        img_b64 = base64.b64encode(buffered.getvalue()).decode('utf-8')

    monto_detectado = extraer_monto_ocr(ruta_local)

    if os.path.exists(ruta_local):
        os.remove(ruta_local)

    context.user_data['monto_actual'] = monto_detectado
    context.user_data['foto_actual_b64'] = img_b64

    keyboard = [
        [InlineKeyboardButton("✅ Confirmar monto", callback_data='confirmar_monto')],
        [InlineKeyboardButton("✏️ Editar monto manualmente", callback_data='editar_monto')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"✅ **Monto detectado:** `${monto_detectado:.2f}`\n\n"
        "¿El valor detectado es correcto?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return ESPERANDO_FOTO

async def solicitar_edicion_monto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        f"✏️ El monto detectado actualmente es `${context.user_data.get('monto_actual', 0.0):.2f}`.\n\n"
        "Por favor, escribe el **nuevo monto correcto** (ejemplo: `1250.50`):",
        parse_mode='Markdown'
    )
    return EDITANDO_MONTO

async def guardar_monto_editado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    texto_ingresado = update.message.text.replace(',', '.')
    
    try:
        nuevo_monto = float(re.sub(r'[^\d.]', '', texto_ingresado))
        context.user_data['monto_actual'] = nuevo_monto
        return await guardar_gasto_en_lista(update, context)
    except ValueError:
        await update.message.reply_text("⚠️ Valor no válido. Por favor ingresa un número correcto (ejemplo: `1500.00`):")
        return EDITANDO_MONTO

async def confirmar_gasto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await guardar_gasto_en_lista(update, context, query=query)

async def guardar_gasto_en_lista(update: Update, context: ContextTypes.DEFAULT_TYPE, query=None):
    if 'gastos' not in context.user_data:
        context.user_data['gastos'] = []

    monto = context.user_data.get('monto_actual', 0.0)
    foto_b64 = context.user_data.get('foto_actual_b64', '')
    
    context.user_data['gastos'].append({
        'monto': monto,
        'foto_b64': foto_b64
    })

    total_acumulado = sum(g['monto'] for g in context.user_data['gastos'])

    keyboard = [
        [InlineKeyboardButton("➕ Cargar otro gasto", callback_data='cargar_otro')],
        [InlineKeyboardButton("📄 Cerrar viaje y generar PDF", callback_data='cerrar_viaje')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    mensaje = (
        f"💰 **Gasto guardado:** ${monto:.2f}\n"
        f"📊 **Total acumulado en {context.user_data.get('titulo_viaje', 'este viaje')}:** `${total_acumulado:.2f}`\n\n"
        "¿Qué deseas hacer a continuación?"
    )

    if query:
        await query.edit_message_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(mensaje, reply_markup=reply_markup, parse_mode='Markdown')

    return ESPERANDO_FOTO

async def manejar_navegacion_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == 'cargar_otro':
        total_actual = sum(g['monto'] for g in context.user_data.get('gastos', []))
        await query.edit_message_text(
            f"📸 **Listo para el siguiente comprobante.**\n"
            f"Llevas acumulado: `${total_actual:.2f}`\n\n"
            "Envíame la foto del siguiente ticket.",
            parse_mode='Markdown'
        )
        return ESPERANDO_FOTO

    elif query.data == 'cerrar_viaje':
        gastos = context.user_data.get('gastos', [])
        titulo = context.user_data.get('titulo_viaje', 'Rendición de Gastos')
        total = sum(g['monto'] for g in gastos)

        await query.edit_message_text("⏳ Generando el reporte PDF con cuadro resumido y comprobantes...")

        filas_tabla = "".join([
            f"<tr><td>Gasto #{i+1}</td><td style='text-align: right;'>${g['monto']:.2f}</td></tr>"
            for i, g in enumerate(gastos)
        ])

        galeria_fotos = "".join([
            f"""
            <div class="comprobante-box">
                <h4>Comprobante #{i+1} - Monto: ${g['monto']:.2f}</h4>
                <img src="data:image/jpeg;base64,{g['foto_b64']}" class="ticket-img"/>
            </div>
            """
            for i, g in enumerate(gastos) if g['foto_b64']
        ])

        html_content = f"""
        <html>
        <head>
            <style>
                body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; padding: 20px; color: #333; }}
                h1 {{ color: #1a237e; margin-bottom: 5px; }}
                h3 {{ color: #555; margin-top: 0; margin-bottom: 25px; }}
                table {{ width: 100%; border-collapse: collapse; margin-bottom: 30px; }}
                th, td {{ padding: 12px 15px; border-bottom: 1px solid #ddd; text-align: left; }}
                th {{ background-color: #f5f5f5; font-weight: bold; }}
                .total-row {{ font-size: 18px; font-weight: bold; background-color: #e8f5e9; color: #2e7d32; }}
                .page-break {{ page-break-before: always; }}
                .comprobante-box {{ margin-bottom: 30px; text-align: center; border: 1px solid #ddd; padding: 10px; border-radius: 5px; }}
                .ticket-img {{ max-width: 90%; max-height: 500px; height: auto; border-radius: 3px; }}
            </style>
        </head>
        <body>
            <h1>Reporte de Gastos</h1>
            <h3>Viaje / Motivo: {titulo}</h3>
            
            <table>
                <thead>
                    <tr>
                        <th>Concepto</th>
                        <th style="text-align: right;">Monto</th>
                    </tr>
                </thead>
                <tbody>
                    {filas_tabla}
                    <tr class="total-row">
                        <td>TOTAL GENERAL</td>
                        <td style="text-align: right;">${total:.2f}</td>
                    </tr>
                </tbody>
            </table>

            <div class="page-break"></div>
            <h2>Anexo: Comprobantes Adjuntos</h2>
            <hr><br>
            {galeria_fotos}
        </body>
        </html>
        """

        pdf_path = f"reporte_{query.message.chat_id}.pdf"
        HTML(string=html_content).write_pdf(pdf_path)

        with open(pdf_path, 'rb') as pdf_file:
            await context.bot.send_document(
                chat_id=query.message.chat_id,
                document=pdf_file,
                filename=f"Reporte_{titulo.replace(' ', '_')}.pdf",
                caption=f"📄 **Viaje Finalizado: {titulo}**\nTotal acumulado: **${total:.2f}**\n\nSi deseas iniciar otro viaje, envía `/start`.",
                parse_mode='Markdown'
            )

        if os.path.exists(pdf_path):
            os.remove(pdf_path)

        context.user_data.clear()
        return ConversationHandler.END

# --- INICIALIZACIÓN Y EVENT LOOP ---
if __name__ == '__main__':
    TOKEN = os.environ.get("TELEGRAM_TOKEN")
    if not TOKEN:
        print("❌ ERROR: No se encontró TELEGRAM_TOKEN", flush=True)
        sys.exit(1)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            TITULO_VIAJE: [MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_titulo)],
            ESPERANDO_FOTO: [
                MessageHandler(filters.PHOTO, procesar_imagen),
                CallbackQueryHandler(solicitar_edicion_monto, pattern='^editar_monto$'),
                CallbackQueryHandler(confirmar_gasto_callback, pattern='^confirmar_monto$'),
                CallbackQueryHandler(manejar_navegacion_botones, pattern='^(cargar_otro|cerrar_viaje)$')
            ],
            EDITANDO_MONTO: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_monto_editado)]
        },
        fallbacks=[CommandHandler("start", start)]
    )

    app.add_handler(conv_handler)

    print(">>> BOT EN MARCHA Y ESCUCHANDO MENSAJES <<<", flush=True)
    app.run_polling(drop_pending_updates=True)
