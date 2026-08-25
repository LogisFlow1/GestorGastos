import os
import sqlite3
import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# Logs para Render
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

TELEGRAM_TOKEN = os.environ.get("8968265973:AAE8xt8pUYov5DQgm3rXFqGevpX3LqiuLzI", "8968265973:AAE8xt8pUYov5DQgm3rXFqGevpX3LqiuLzI")

# --- BASE DE DATOS LOCAL ---
def init_db():
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS viajes (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, destino TEXT, fecha_inicio TEXT, activo INTEGER DEFAULT 1)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS gastos (id INTEGER PRIMARY KEY AUTOINCREMENT, viaje_id INTEGER, monto REAL, categoria TEXT, descripcion TEXT, foto_path TEXT, fecha TEXT)''')
    conn.commit()
    conn.close()

init_db()

def get_viaje_activo(user_id):
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, destino, fecha_inicio FROM viajes WHERE user_id = ? AND activo = 1", (user_id,))
    viaje = cursor.fetchone()
    conn.close()
    return viaje

# --- TECLADO FIJO INFERIOR ---
def menu_teclado():
    # Eliminamos el persistent=True que causaba el conflicto
    keyboard = [[KeyboardButton("📊 Ver Gastos"), KeyboardButton("🏁 Cerrar Viaje")]]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

# --- INICIO DE VIAJE ---
async def saludar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    viaje = get_viaje_activo(user_id)
    if viaje:
        await update.message.reply_text(
            f"👋 ¡Hola! Tienes un viaje activo a: **{viaje[1]}**.\n\n"
            "📸 Envíame la foto del comprobante y en el comentario pon: `Monto, Descripción`.", 
            parse_mode="Markdown", reply_markup=menu_teclado()
        )
    else:
        await update.message.reply_text("👋 ¡Hola! Para iniciar un viaje nuevo, **escribe el lugar de destino** aquí abajo (Ejemplo: Rosario).")

async def iniciar_viaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
        f"✈️ **Viaje a {destino} iniciado.**\n\n"
        "A partir de ahora, manda las fotos de tus comprobantes.\n"
        "En el texto de la foto solo pon: `Monto, Descripción`.\n\n"
        "👇 _Usa los botones de abajo para navegar._", parse_mode="Markdown", reply_markup=menu_teclado()
    )

# --- PROCESAMIENTO DE FOTOS Y CATEGORÍAS ---
async def procesar_foto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if 'esperando_foto' in context.user_data:
            gasto_id = context.user_data['esperando_foto']
            photo_file = await update.message.photo[-1].get_file()
            os.makedirs("comprobantes", exist_ok=True)
            foto_path = f"comprobantes/{photo_file.file_id}.jpg"
            await photo_file.download_to_drive(foto_path)
            
            conn = sqlite3.connect("gastos.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE gastos SET foto_path = ? WHERE id = ?", (foto_path, gasto_id))
            conn.commit()
            conn.close()
            del context.user_data['esperando_foto']
            return await update.message.reply_text(f"✅ ¡Listo! Foto actualizada para el gasto ID {gasto_id}.")

        user_id = update.effective_user.id
        viaje = get_viaje_activo(user_id)
        if not viaje: return await update.message.reply_text("⚠️ No tienes ningún viaje activo. Escribe el nombre del destino para iniciar uno.")

        caption = update.message.caption or ""
        partes = caption.split(',', 1)
        if len(partes) < 2:
            return await update.message.reply_text(
                "❌ **Faltan datos.** Debes mandar la foto y poner de comentario:\n"
                "`Monto, Descripción`\n_(Ej: 2500, Taxi al hotel)_", parse_mode="Markdown"
            )
        try:
            monto = float(partes[0].replace('$', '').replace(',', '.').strip())
            descripcion = partes[1].strip()
        except ValueError:
            return await update.message.reply_text("❌ El monto no es válido. Escribe solo números (sin puntos).")

        photo_file = await update.message.photo[-1].get_file()
        os.makedirs("comprobantes", exist_ok=True)
        foto_path = f"comprobantes/{photo_file.file_id}.jpg"
        await photo_file.download_to_drive(foto_path)

        conn = sqlite3.connect("gastos.db")
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO gastos (viaje_id, monto, categoria, descripcion, foto_path, fecha) VALUES (?, ?, ?, ?, ?, ?)",
            (viaje[0], monto, "PENDIENTE", descripcion, foto_path, datetime.now().strftime("%Y-%m-%d %H:%M"))
        )
        gasto_id = cursor.lastrowid
        conn.commit()
        conn.close()

        teclado_categorias = [
            [InlineKeyboardButton("🚗 Transporte", callback_data=f"cat_Transporte_{gasto_id}")],
            [InlineKeyboardButton("🍔 Comida", callback_data=f"cat_Comida_{gasto_id}")],
            [InlineKeyboardButton("📎 Otro", callback_data=f"cat_Otro_{gasto_id}")]
        ]
        await update.message.reply_text(f"⏳ Gasto de **${monto:.2f}** ({descripcion}).\n👉 **Selecciona la categoría:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(teclado_categorias))
    except Exception as e:
        logger.error(f"Error procesando foto: {e}")
        await update.message.reply_text(f"❌ Ocurrió un error al procesar la foto.")

async def boton_categoria(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    datos = query.data.split('_')
    categoria = datos[1]
    gasto_id = datos[2]

    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE gastos SET categoria = ? WHERE id = ?", (categoria, gasto_id))
    conn.commit()
    conn.close()
    await query.edit_message_text(text=f"✅ Guardado como **{categoria}**.", parse_mode="Markdown")

# --- EDICIÓN Y GESTIÓN CON BOTONES ---
async def accion_ver_gastos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    viaje = get_viaje_activo(update.effective_user.id)
    if not viaje: return await update.message.reply_text("⚠️ No tienes ningún viaje activo.")
    
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, monto, categoria, descripcion FROM gastos WHERE viaje_id = ?", (viaje[0],))
    gastos = cursor.fetchall()
    conn.close()

    if not gastos: return await update.message.reply_text("Aún no tienes gastos registrados.")

    texto = f"📋 **Resumen actual:**\n\n"
    total = sum(g[1] for g in gastos)
    for g in gastos: texto += f"🔹 `ID {g[0]}` | **${g[1]:.2f}** | [{g[2]}] {g[3][:20]}\n"
    texto += f"\n💰 **Total:** ${total:.2f}"
    
    teclado = [[InlineKeyboardButton("🛠️ Gestionar (Editar/Borrar)", callback_data="gestionar_lista")]]
    await update.message.reply_text(texto, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(teclado))

async def callback_gestionar_lista(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    viaje = get_viaje_activo(update.effective_user.id)
    
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, monto, descripcion FROM gastos WHERE viaje_id = ?", (viaje[0],))
    gastos = cursor.fetchall()
    conn.close()

    keyboard = []
    for g in gastos:
        keyboard.append([InlineKeyboardButton(f"ID {g[0]} - ${g[1]} ({g[2][:15]}...)", callback_data=f"opciones_{g[0]}")])
    
    await query.edit_message_text("👇 **Toca el gasto que deseas corregir:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_opciones_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gasto_id = query.data.split('_')[1]
    
    keyboard = [
        [InlineKeyboardButton("💰 Cambiar Monto", callback_data=f"editmonto_{gasto_id}"), InlineKeyboardButton("🏷️ Categoría", callback_data=f"editcat_{gasto_id}")],
        [InlineKeyboardButton("🖼️ Cambiar Foto", callback_data=f"editfoto_{gasto_id}"), InlineKeyboardButton("🗑️ Eliminar", callback_data=f"eliminar_{gasto_id}")]
    ]
    await query.edit_message_text(f"🛠️ **Opciones para el gasto ID {gasto_id}:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))

async def callback_eliminar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gasto_id = query.data.split('_')[1]
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
    conn.commit()
    conn.close()
    await query.edit_message_text(f"🗑️ El gasto ID {gasto_id} ha sido eliminado.")

async def callback_editcat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gasto_id = query.data.split('_')[1]
    teclado = [
        [InlineKeyboardButton("🚗 Transporte", callback_data=f"cat_Transporte_{gasto_id}")],
        [InlineKeyboardButton("🍔 Comida", callback_data=f"cat_Comida_{gasto_id}")],
        [InlineKeyboardButton("📎 Otro", callback_data=f"cat_Otro_{gasto_id}")]
    ]
    await query.edit_message_text("👉 **Selecciona la nueva categoría:**", parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(teclado))

async def callback_editfoto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gasto_id = query.data.split('_')[1]
    context.user_data['esperando_foto'] = gasto_id
    await query.edit_message_text(f"📸 **Envía la nueva foto ahora.**\n(Reemplazará la anterior para el ID {gasto_id})", parse_mode="Markdown")

async def callback_editmonto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    gasto_id = query.data.split('_')[1]
    context.user_data['esperando_monto'] = gasto_id
    await query.edit_message_text(f"✏️ **Escribe en el chat el nuevo monto** para el gasto ID {gasto_id} (solo el número):", parse_mode="Markdown")

# --- LECTURA INTELIGENTE DE TEXTO ---
async def procesar_texto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        txt = update.message.text
        
        # 1. ¿Está esperando un nuevo monto?
        if 'esperando_monto' in context.user_data:
            gasto_id = context.user_data['esperando_monto']
            try:
                nuevo_monto = float(txt.replace('$', '').replace(',', '.').strip())
                conn = sqlite3.connect("gastos.db")
                cursor = conn.cursor()
                cursor.execute("UPDATE gastos SET monto = ? WHERE id = ?", (nuevo_monto, gasto_id))
                conn.commit()
                conn.close()
                del context.user_data['esperando_monto']
                await update.message.reply_text(f"✅ ¡Listo! Monto corregido a **${nuevo_monto:.2f}**.")
            except ValueError:
                await update.message.reply_text("❌ Formato incorrecto. Escribe solo el número (Ej: 2500):")
            return

        # 2. Reaccionar a botones fijos
        if txt == "📊 Ver Gastos": return await accion_ver_gastos(update, context)
        if txt == "🏁 Cerrar Viaje": return await accion_cerrar_viaje(update, context)

        # 3. MODO INTUITIVO: Si escribe algo y NO tiene viaje, es el DESTINO.
        user_id = update.effective_user.id
        viaje = get_viaje_activo(user_id)
        
        if not viaje:
            destino = txt
            fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
            conn = sqlite3.connect("gastos.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE viajes SET activo = 0 WHERE user_id = ?", (user_id,))
            cursor.execute("INSERT INTO viajes (user_id, destino, fecha_inicio, activo) VALUES (?, ?, ?, 1)", (user_id, destino, fecha))
            conn.commit()
            conn.close()

            await update.message.reply_text(
                f"✈️ **Viaje a {destino} iniciado.**\n\n"
                "A partir de ahora, manda las fotos de tus comprobantes.\n"
                "En el texto de la foto solo pon: `Monto, Descripción`.\n\n"
                "👇 _Usa los botones de abajo para navegar._", parse_mode="Markdown", reply_markup=menu_teclado()
            )
        else:
            await update.message.reply_text("💡 Para agregar un gasto, envíame la **FOTO** del ticket y escribe en el comentario `Monto, Descripción`.\nSi quieres ver el resumen, usa los botones inferiores.")
    except Exception as e:
        logger.error(f"Error procesando texto: {e}")
        await update.message.reply_text(f"❌ Hubo un error de procesamiento. Intenta de nuevo.")

# --- REPORTE FINAL ---
async def accion_cerrar_viaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = update.effective_user.id
        viaje = get_viaje_activo(user_id)
        if not viaje: return await update.message.reply_text("No tienes ningún viaje activo.")
        
        await update.message.reply_text("Generando tu PDF agrupado, un momento... ⏳")
        viaje_id, destino, fecha_inicio = viaje
        conn = sqlite3.connect("gastos.db")
        cursor = conn.cursor()
        cursor.execute("SELECT id, monto, categoria, descripcion, foto_path, fecha FROM gastos WHERE viaje_id = ?", (viaje_id,))
        gastos = cursor.fetchall()

        if not gastos:
            conn.close()
            return await update.message.reply_text("No registraste gastos.")

        pdf_filename = f"Reporte_Viaje_{viaje_id}.pdf"
        doc = SimpleDocTemplate(pdf_filename, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
        styles = getSampleStyleSheet()
        story = [Paragraph("<b>Reporte de Rendición de Gastos</b>", styles['Title']), Paragraph(f"<b>Destino:</b> {destino} | <b>Fecha:</b> {fecha_inicio}", styles['Normal']), Spacer(1, 15)]

        tabla_data = [["ID", "Fecha", "Categoría", "Descripción", "Monto ($)"]]
        total_monto = 0.0

        agrupados = {"Transporte": [], "Comida": [], "Otro": []}
        for g in gastos: agrupados[g[2] if g[2] in agrupados else "Otro"].append(g)

        for cat in ["Transporte", "Comida", "Otro"]:
            if agrupados[cat]:
                tabla_data.append(["", "", f"--- {cat.upper()} ---", "", ""])
                for g in agrupados[cat]:
                    tabla_data.append([str(g[0]), g[5], g[2], g[3], f"${g[1]:.2f}"])
                    total_monto += g[1]

        tabla_data.append(["", "", "", "<b>TOTAL:</b>", f"<b>${total_monto:.2f}</b>"])
        t = Table(tabla_data, colWidths=[30, 80, 80, 240, 100])
        t.setStyle(TableStyle([('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1A365D")), ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke), ('ALIGN', (0,0), (-1,-1), 'LEFT'), ('GRID', (0,0), (-1,-1), 0.5, colors.grey)]))
        story.append(t)
        
        story.append(PageBreak())
        story.append(Paragraph("<b>Anexo de Comprobantes</b>", styles['Heading2']))
        cajas_fotos = []
        for g in gastos:
            if g[4] and os.path.exists(g[4]):
                try: cajas_fotos.append([RLImage(g[4], width=240, height=260), Paragraph(f"<b>[ID {g[0]}]</b> {g[2]} - ${g[1]:.2f}<br/>{g[3][:35]}", ParagraphStyle('Img', fontSize=8, alignment=1))])
                except: pass

        filas = [cajas_fotos[i:i+2] if len(cajas_fotos[i:i+2])==2 else [cajas_fotos[i], ""] for i in range(0, len(cajas_fotos), 2)]
        if filas:
            gt = Table(filas, colWidths=[270, 270])
            gt.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER')]))
            story.append(gt)

        doc.build(story)
        cursor.execute("UPDATE viajes SET activo = 0 WHERE id = ?", (viaje_id,))
        conn.commit()
        conn.close()

        await update.message.reply_document(document=open(pdf_filename, 'rb'), caption=f"🧾 **Rendición Finalizada**\n**Total:** ${total_monto:.2f}")
    except Exception as e:
        logger.error(f"Error generando PDF: {e}")
        await update.message.reply_text("❌ Ocurrió un error al generar el PDF.")

# --- ARRANQUE PRINCIPAL ---
if __name__ == '__main__':
    # --- Servidor Falso para Render ---
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    
    class DummyHandler(BaseHTTPRequestHandler):
        def do_GET(self): 
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot OK")
            
    def run_dummy_server():
        port = int(os.environ.get("PORT", 8080))
        server = HTTPServer(("0.0.0.0", port), DummyHandler)
        server.serve_forever()
        
    threading.Thread(target=run_dummy_server, daemon=True).start()
    # ----------------------------------

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", saludar_usuario))
    app.add_handler(CommandHandler("iniciar_viaje", iniciar_viaje))
    # Filtro solo para "Hola", "Buenas", "Buen dia" para que no interfiera
    app.add_handler(MessageHandler(filters.Regex(r'(?i)^(hola|buenas|buen dia)'), saludar_usuario))
    
    app.add_handler(MessageHandler(filters.PHOTO, procesar_foto))
    
    # Filtro inteligente de texto
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, procesar_texto))
    
    # Callbacks de los botones (Edición, Categorías, Borrar)
    app.add_handler(CallbackQueryHandler(boton_categoria, pattern='^cat_'))
    app.add_handler(CallbackQueryHandler(callback_gestionar_lista, pattern='^gestionar_lista$'))
    app.add_handler(CallbackQueryHandler(callback_opciones_gasto, pattern='^opciones_'))
    app.add_handler(CallbackQueryHandler(callback_eliminar, pattern='^eliminar_'))
    app.add_handler(CallbackQueryHandler(callback_editcat, pattern='^editcat_'))
    app.add_handler(CallbackQueryHandler(callback_editfoto, pattern='^editfoto_'))
    app.add_handler(CallbackQueryHandler(callback_editmonto, pattern='^editmonto_'))

    logger.info("Bot Iniciado...")
    app.run_polling()
