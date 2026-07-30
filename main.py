import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

# Servidor web ficticio para engañar al Web Service de Render
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot activo")

def run_dummy_server():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    server.serve_forever()

# Iniciar el servidor web en segundo plano antes del bot
threading.Thread(target=run_dummy_server, daemon=True).start()

# --- AQUÍ SIGUE TU CÓDIGO HABITUAL DEL BOT ---
import os
import re
import datetime
from PIL import Image
import pytesseract
from weasyprint import HTML
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
    filters,
)

# Estado de conversación
AguardandoCiudad = range(1)

# Diccionario en memoria para las sesiones de usuario
USER_SESSIONS = {}

def extract_ticket_info(image_path: str) -> dict:
    """Extrae importe y fecha usando Tesseract OCR"""
    try:
        img = Image.open(image_path).convert('L')
        text = pytesseract.image_to_string(img, lang='spa+eng')
        
        monto = 0.0
        for line in text.split('\n'):
            line_upper = line.upper()
            if any(k in line_upper for k in ['TOTAL', 'IMPORTE', 'PAGO', 'FINAL', 'SUMA', 'COBRO']):
                matches = re.findall(r'([0-9]+(?:[.,][0-9]{1,2})?)', line)
                if matches:
                    try:
                        monto = float(matches[-1].replace('.', '').replace(',', '.'))
                        break
                    except ValueError:
                        pass
        
        fecha = datetime.date.today().strftime("%d/%m/%Y")
        date_match = re.search(r'(\d{2}[/.-]\d{2}[/.-]\d{4}|\d{4}[/.-]\d{2}[/.-]\d{2})', text)
        if date_match:
            fecha = date_match.group(1).replace('-', '/')
            
        return {"monto": monto, "fecha": fecha}
    except Exception as e:
        print(f"Error procesando OCR: {e}")
        return {"monto": 0.0, "fecha": datetime.date.today().strftime("%d/%m/%Y")}

def generar_pdf_reporte(viaje_data: dict, output_pdf_path: str):
    """Genera la tabla HTML + Anexo de comprobantes y lo convierte a PDF"""
    ciudad = viaje_data.get("ciudad", "Viaje de Trabajo")
    fecha_inicio = viaje_data.get("fecha_inicio", datetime.date.today().strftime("%d/%m/%Y"))
    gastos = viaje_data.get("gastos", [])
    total_general = sum(g["monto"] for g in gastos)

    filas_html = ""
    for idx, gasto in enumerate(gastos, start=1):
        filas_html += f"""
        <tr>
            <td style="text-align: center;">{idx}</td>
            <td style="text-align: center;">{gasto['fecha']}</td>
            <td>{gasto['concepto']}</td>
            <td style="text-align: right; font-weight: bold;">${gasto['monto']:,.2f}</td>
        </tr>
        """

    anexo_html = ""
    if gastos:
        anexo_html += '<div style="page-break-before: always;"></div>'
        anexo_html += '<h2 style="color: #0d3b66; border-bottom: 2px solid #0d3b66; padding-bottom: 6px;">📷 Anexo de Comprobantes</h2>'
        for idx, gasto in enumerate(gastos, start=1):
            if gasto.get('foto_path') and os.path.exists(gasto['foto_path']):
                abs_path = os.path.abspath(gasto['foto_path'])
                anexo_html += f"""
                <div style="margin-bottom: 25px; text-align: center; border: 1px solid #cbd5e1; padding: 12px; background: #ffffff; border-radius: 6px; page-break-inside: avoid;">
                    <p style="margin-top:0; font-weight: bold; text-align: left;">Comprobante #{idx} - {gasto['concepto']} (${gasto['monto']:,.2f})</p>
                    <img src="file://{abs_path}" style="max-width: 100%; max-height: 420px; object-fit: contain;" />
                </div>
                """

    html_document = f"""
    <!DOCTYPE html>
    <html lang="es">
    <head>
        <meta charset="UTF-8">
        <style>
            @page {{ size: A4; margin: 15mm 12mm; background-color: #f8fafc; }}
            body {{ font-family: sans-serif; color: #1e293b; margin: 0; font-size: 11pt; }}
            .header-banner {{ background-color: #0d3b66; color: #ffffff; padding: 20px; border-radius: 8px; margin-bottom: 20px; }}
            .header-banner h1 {{ margin: 0; font-size: 20pt; }}
            .summary-box {{ background-color: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 15px; margin-bottom: 20px; }}
            .table-gastos {{ width: 100%; border-collapse: collapse; background-color: #ffffff; border-radius: 6px; border: 1px solid #cbd5e1; margin-bottom: 20px; }}
            .table-gastos th {{ background-color: #0d3b66; color: #ffffff; padding: 10px; font-size: 10pt; text-transform: uppercase; }}
            .table-gastos td {{ padding: 10px; border-bottom: 1px solid #e2e8f0; font-size: 10.5pt; }}
            .table-gastos tr:nth-child(even) {{ background-color: #f1f5f9; }}
            .total-row {{ background-color: #e2e8f0 !important; font-weight: bold; font-size: 11.5pt; }}
        </style>
    </head>
    <body>
        <div class="header-banner">
            <h1>Rendición de Gastos de Viaje</h1>
            <p>Reporte Oficial de Viáticos</p>
        </div>
        <div class="summary-box">
            <table style="width: 100%;">
                <tr>
                    <td><strong>Destino:</strong> {ciudad}</td>
                    <td style="text-align: right;"><strong>Inicio:</strong> {fecha_inicio}</td>
                </tr>
                <tr>
                    <td><strong>Tickets:</strong> {len(gastos)}</td>
                    <td style="text-align: right;"><strong>Cierre:</strong> {datetime.date.today().strftime("%d/%m/%Y")}</td>
                </tr>
            </table>
        </div>
        <h2>Resumen de Gastos</h2>
        <table class="table-gastos">
            <thead>
                <tr><th style="width:8%;">#</th><th style="width:20%;">Fecha</th><th style="width:47%;">Detalle</th><th style="width:25%; text-align:right;">Monto</th></tr>
            </thead>
            <tbody>
                {filas_html}
                <tr class="total-row">
                    <td colspan="3" style="text-align: right; padding: 12px;">TOTAL RENDICIÓN:</td>
                    <td style="text-align: right; padding: 12px; color: #0d3b66;">${total_general:,.2f}</td>
                </tr>
            </tbody>
        </table>
        {anexo_html}
    </body>
    </html>
    """
    HTML(string=html_document).write_pdf(output_pdf_path)

# ----------------- HANDLERS ----------------- #

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 ¡Hola! Soy tu Asistente de Gastos.\n\n"
        "Comandos:\n"
        "📌 /nuevo_viaje - Inicia un nuevo viaje\n"
        "📸 Envía fotos de tickets para sumarlos\n"
        "📊 /estado - Ver total acumulado\n"
        "📑 /cerrar_viaje - Genera reporte PDF"
    )

async def nuevo_viaje_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    USER_SESSIONS[user_id] = {
        "ciudad": None,
        "fecha_inicio": datetime.date.today().strftime("%d/%m/%Y"),
        "gastos": [],
        "temp_gasto": {}
    }
    await update.message.reply_text("🏙️ Por favor, escribe la **Ciudad / Destino** de este viaje:")
    return AguardandoCiudad

async def guardar_ciudad(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    ciudad = update.message.text.strip()
    USER_SESSIONS[user_id]["ciudad"] = ciudad
    await update.message.reply_text(f"✅ Viaje a **{ciudad}** registrado. ¡Comienza a enviarme la foto de cada ticket!")
    return ConversationHandler.END

async def procesar_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = USER_SESSIONS.get(user_id)

    if not session or not session.get("ciudad"):
        await update.message.reply_text("⚠️ No tienes un viaje activo. Inicia uno con /nuevo_viaje")
        return

    photo_file = await update.message.photo[-1].get_file()
    os.makedirs("tickets_temp", exist_ok=True)
    filename = f"tickets_temp/ticket_{user_id}_{len(session['gastos']) + 1}.jpg"
    await photo_file.download_to_drive(filename)

    msg_espera = await update.message.reply_text("🔍 Escaneando comprobante con OCR...")
    info = extract_ticket_info(filename)
    
    session["temp_gasto"] = {
        "foto_path": filename,
        "monto": info["monto"],
        "fecha": info["fecha"],
        "concepto": "Gasto General"
    }

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirmar Monto", callback_data="confirmar_ok"),
            InlineKeyboardButton("✏️ Corregir Monto", callback_data="modificar_monto")
        ]
    ]
    
    monto_str = f"${info['monto']:,.2f}" if info['monto'] > 0 else "No detectado"
    await msg_espera.edit_text(
        f"📄 **Lector OCR:**\n• Fecha: {info['fecha']}\n• Monto: `{monto_str}`\n\n¿Es correcto?",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def callback_confirmacion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "confirmar_ok":
        await query.edit_message_text("✍️ Escribe un breve **detalle o concepto** (ej: *Almuerzo, Taxi, Hotel*):", parse_mode="Markdown")
        context.user_data["esperando_detalle"] = True
    elif query.data == "modificar_monto":
        await query.edit_message_text("💵 Ingresa el **monto correcto** solo en números (ej: `15500.50`):", parse_mode="Markdown")
        context.user_data["esperando_monto"] = True

async def recibir_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = USER_SESSIONS.get(user_id)

    if context.user_data.get("esperando_monto"):
        try:
            monto_val = float(update.message.text.replace("$", "").replace(".", "").replace(",", ".").strip())
            session["temp_gasto"]["monto"] = monto_val
            context.user_data["esperando_monto"] = False
            await update.message.reply_text("👍 Monto corregido. Ahora escribe el **concepto o detalle**:")
            context.user_data["esperando_detalle"] = True
        except ValueError:
            await update.message.reply_text("❌ Ingresa solo valores numéricos válidos:")

    elif context.user_data.get("esperando_detalle"):
        concepto = update.message.text.strip()
        session["temp_gasto"]["concepto"] = concepto
        
        session["gastos"].append(session["temp_gasto"].copy())
        session["temp_gasto"] = {}
        context.user_data["esperando_detalle"] = False

        total = sum(g["monto"] for g in session["gastos"])
        await update.message.reply_text(f"✅ **Gasto Guardado**\n• {concepto}\n• Acumulado: **${total:,.2f}** ({len(session['gastos'])} tickets)")

async def estado_viaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = USER_SESSIONS.get(user_id)
    if not session or not session.get("ciudad"):
        await update.message.reply_text("ℹ️ No hay viajes activos.")
        return
    total = sum(g["monto"] for g in session["gastos"])
    await update.message.reply_text(f"📊 Viaje activo a **{session['ciudad']}**\nTotal acumulado: **${total:,.2f}** ({len(session['gastos'])} comprobantes)")

async def cerrar_viaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = USER_SESSIONS.get(user_id)

    if not session or not session.get("ciudad") or not session["gastos"]:
        await update.message.reply_text("⚠️ No tienes gastos registrados en este viaje.")
        return

    msg = await update.message.reply_text("⏳ Compilando tabla y fotos en el PDF...")

    pdf_filename = f"Reporte_{session['ciudad'].replace(' ', '_')}.pdf"
    generar_pdf_reporte(session, pdf_filename)

    with open(pdf_filename, "rb") as pdf_file:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=pdf_file,
            filename=pdf_filename,
            caption=f"📑 **Rendición Lista**\n📍 {session['ciudad']}\n💰 Total: **${sum(g['monto'] for g in session['gastos']):,.2f}**",
            parse_mode="Markdown"
        )

    await msg.delete()
    USER_SESSIONS[user_id] = {}

def main():
    # ⚠️ REEMPLAZA ESTE TEXTO CON EL TOKEN QUE TE DIO BOTFATHER EN EL PASO 1
    TOKEN = "8968265973:AAE8xt8pUYov5DQgm3rXFqGevpX3LqiuLzI"
    
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("nuevo_viaje", nuevo_viaje_start)],
        states={AguardandoCiudad: [MessageHandler(filters.TEXT & ~filters.COMMAND, guardar_ciudad)]},
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("estado", estado_viaje))
    app.add_handler(CommandHandler("cerrar_viaje", cerrar_viaje))
    app.add_handler(MessageHandler(filters.PHOTO, procesar_foto))
    app.add_handler(CallbackQueryHandler(callback_confirmacion))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, recibir_texto))

    print("🤖 Bot listo y corriendo...")
    app.run_polling()

if __name__ == "__main__":
    main()
