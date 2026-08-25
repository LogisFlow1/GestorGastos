import os
import sqlite3
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

# ReportLab para la generación del PDF
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, Image as RLImage, PageBreak
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors

TELEGRAM_TOKEN = os.environ.get("8968265973:AAE8xt8pUYov5DQgm3rXFqGevpX3LqiuLzI", "8968265973:AAE8xt8pUYov5DQgm3rXFqGevpX3LqiuLzI")

# --- BASE DE DATOS LOCAL (SQLite) ---
def init_db():
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

init_db()

def get_viaje_activo(user_id):
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, destino, fecha_inicio FROM viajes WHERE user_id = ? AND activo = 1", (user_id,))
    viaje = cursor.fetchone()
    conn.close()
    return viaje

# --- SALUDO E INICIO ---

async def saludar_usuario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_name = update.effective_user.first_name
    viaje = get_viaje_activo(user_id)

    if viaje:
        _, destino, fecha = viaje
        mensaje = (
            f"👋 ¡Hola, {user_name}!\n\n"
            f"Tienes un viaje activo hacia: **{destino}**.\n\n"
            "📸 **Sube una foto** con el formato: `Monto, Categoría, Descripción`.\n"
            "📋 Escribe `/gastos` para ver el resumen actual.\n"
            "🏁 Escribe `/finalizar_viaje` para generar tu PDF."
        )
    else:
        mensaje = (
            f"👋 ¡Hola, {user_name}! Soy tu bot de gastos.\n\n"
            "Para comenzar un nuevo viaje, escribe:\n"
            "`/iniciar_viaje <Destino>`"
        )
    await update.message.reply_text(mensaje, parse_mode="Markdown")

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
        f"✈️ **Viaje iniciado** a: *{destino}*\n\n"
        "Envía tus fotos. En el texto de la foto debes escribir exactamente así:\n"
        "`Monto, Categoría, Descripción`\n"
        "_(Ejemplo: `1500, Comida, Cena de negocios`)_\n\n"
        "**Comandos:**\n"
        "• `/gastos` : Ver resumen\n"
        "• `/editar_monto <ID> <monto>` : Corregir un error\n"
        "• `/eliminar <ID>` : Borrar gasto\n"
        "• `/finalizar_viaje` : Generar PDF",
        parse_mode="Markdown"
    )

# --- EDICIÓN Y LISTADO ---

async def listar_gastos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    viaje = get_viaje_activo(update.effective_user.id)
    if not viaje:
        await update.message.reply_text("⚠️ No tienes ningún viaje activo.")
        return

    viaje_id, destino, _ = viaje
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, monto, categoria, descripcion FROM gastos WHERE viaje_id = ?", (viaje_id,))
    gastos = cursor.fetchall()
    conn.close()

    if not gastos:
        await update.message.reply_text("No hay gastos registrados.")
        return

    texto = f"📋 **Gastos registrados ({destino}):**\n\n"
    total = 0.0
    for g in gastos:
        gid, monto, cat, desc = g
        texto += f"🔹 `ID: {gid}` | **${monto:.2f}** | [{cat}] {desc}\n"
        total += monto
    texto += f"\n💰 **Total acumulado:** ${total:.2f}"
    await update.message.reply_text(texto, parse_mode="Markdown")

async def eliminar_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        return await update.message.reply_text("Uso: `/eliminar <ID>`")
    try:
        gasto_id = int(context.args[0])
    except ValueError:
        return await update.message.reply_text("El ID debe ser un número.")

    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT foto_path FROM gastos WHERE id = ?", (gasto_id,))
    res = cursor.fetchone()

    if res and res[0] and os.path.exists(res[0]):
        try: os.remove(res[0])
        except OSError: pass

    cursor.execute("DELETE FROM gastos WHERE id = ?", (gasto_id,))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"🗑️ Gasto `{gasto_id}` eliminado.")

async def editar_monto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if len(context.args) < 2:
        return await update.message.reply_text("Uso: `/editar_monto <ID> <monto>`")
    try:
        gasto_id = int(context.args[0])
        nuevo_monto = float(context.args[1].replace(',', '.'))
    except ValueError:
        return await update.message.reply_text("Formato incorrecto.")

    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("UPDATE gastos SET monto = ? WHERE id = ?", (nuevo_monto, gasto_id))
    conn.commit()
    conn.close()
    await update.message.reply_text(f"✏️ Monto del ID `{gasto_id}` actualizado a **${nuevo_monto:.2f}**")

# --- PROCESAMIENTO TEXTUAL (SIN IA) ---

async def procesar_gasto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    viaje = get_viaje_activo(user_id)
    if not viaje:
        return await update.message.reply_text("⚠️ No hay viaje activo.")

    viaje_id = viaje[0]
    caption = update.message.caption or ""

    # Reemplazar foto
    if caption.startswith("/cambiar_foto"):
        partes = caption.split()
        if len(partes) > 1 and partes[1].isdigit():
            gasto_id_reemplazo = int(partes[1])
            photo_file = await update.message.photo[-1].get_file()
            foto_path = f"comprobantes/{photo_file.file_id}.jpg"
            await photo_file.download_to_drive(foto_path)
            
            conn = sqlite3.connect("gastos.db")
            cursor = conn.cursor()
            cursor.execute("UPDATE gastos SET foto_path = ? WHERE id = ?", (foto_path, gasto_id_reemplazo))
            conn.commit()
            conn.close()
            return await update.message.reply_text(f"🖼️ Foto del ID `{gasto_id_reemplazo}` actualizada.")

    # Descarga de la foto principal
    photo_file = await update.message.photo[-1].get_file()
    os.makedirs("comprobantes", exist_ok=True)
    foto_path = f"comprobantes/{photo_file.file_id}.jpg"
    await photo_file.download_to_drive(foto_path)

    # Extraer variables con Python puro
    try:
        datos_texto = caption.split(',')
        if len(datos_texto) < 3:
            raise ValueError()
        
        monto = float(datos_texto[0].strip().replace('$', ''))
        categoria = datos_texto[1].strip()
        descripcion = ",".join(datos_texto[2:]).strip()
    except Exception:
        if os.path.exists(foto_path):
            os.remove(foto_path)
        return await update.message.reply_text(
            "❌ **Error de formato.**\n"
            "Debes enviar la foto escribiendo en el comentario:\n"
            "`Monto, Categoría, Descripción`\n\n"
            "💡 _Ejemplo:_ `1500, Transporte, Taxi al hotel`",
            parse_mode="Markdown"
        )

    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO gastos (viaje_id, monto, categoria, descripcion, foto_path, fecha) VALUES (?, ?, ?, ?, ?, ?)",
        (viaje_id, monto, categoria, descripcion, foto_path, datetime.now().strftime("%Y-%m-%d %H:%M"))
    )
    nuevo_id = cursor.lastrowid
    conn.commit()
    conn.close()

    await update.message.reply_text(f"✅ **Gasto Guardado (ID: {nuevo_id})**\n💵 **${monto:.2f}** | 🏷️ {categoria}\n📝 {descripcion}")

# --- REPORTE PDF (CUADRÍCULA 2x2) ---

async def finalizar_viaje(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    viaje = get_viaje_activo(user_id)
    if not viaje:
        return await update.message.reply_text("No tienes ningún viaje activo.")

    viaje_id, destino, fecha_inicio = viaje
    conn = sqlite3.connect("gastos.db")
    cursor = conn.cursor()
    cursor.execute("SELECT id, monto, categoria, descripcion, foto_path, fecha FROM gastos WHERE viaje_id = ?", (viaje_id,))
    gastos = cursor.fetchall()

    if not gastos:
        conn.close()
        return await update.message.reply_text("No registraste ningún gasto.")

    pdf_filename = f"Reporte_Viaje_{viaje_id}.pdf"
    doc = SimpleDocTemplate(pdf_filename, pagesize=letter, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    story = []

    story.append(Paragraph("<b>Reporte de Rendición de Gastos</b>", styles['Title']))
    story.append(Paragraph(f"<b>Destino:</b> {destino} | <b>Fecha:</b> {fecha_inicio}", styles['Normal']))
    story.append(Spacer(1, 15))

    tabla_data = [["ID", "Fecha", "Categoría", "Descripción", "Monto ($)"]]
    total_monto = 0.0

    for g in gastos:
        gid, monto, cat, desc, foto, fecha = g
        tabla_data.append([str(gid), fecha, cat, desc, f"${monto:.2f}"])
        total_monto += monto

    tabla_data.append(["", "", "", "<b>TOTAL:</b>", f"<b>${total_monto:.2f}</b>"])
    
    t = Table(tabla_data, colWidths=[30, 80, 80, 240, 100])
    t.setStyle(TableStyle([
        ('BACKGROUND', (0,0), (-1,0), colors.HexColor("#1A365D")),
        ('TEXTCOLOR', (0,0), (-1,0), colors.whitesmoke),
        ('ALIGN', (0,0), (-1,-1), 'LEFT'),
        ('GRID', (0,0), (-1,-1), 0.5, colors.grey)
    ]))
    story.append(t)
    
    story.append(PageBreak())
    story.append(Paragraph("<b>Anexo de Comprobantes</b>", styles['Heading2']))
    story.append(Spacer(1, 10))

    label_style = ParagraphStyle('ImgLabel', parent=styles['Normal'], fontSize=8, alignment=1)
    cajas_fotos = []
    
    for g in gastos:
        gid, monto, cat, desc, foto_path, _ = g
        if foto_path and os.path.exists(foto_path):
            try:
                img_obj = RLImage(foto_path, width=240, height=260)
                etiqueta = Paragraph(f"<b>[ID: {gid}]</b> {cat} - ${monto:.2f}<br/>{desc[:35]}", label_style)
                cajas_fotos.append([img_obj, etiqueta])
            except: pass

    filas_grid = []
    for i in range(0, len(cajas_fotos), 2):
        par = cajas_fotos[i:i+2]
        filas_grid.append(par if len(par) == 2 else [par[0], ""])

    if filas_grid:
        grid_table = Table(filas_grid, colWidths=[270, 270])
        grid_table.setStyle(TableStyle([('ALIGN', (0,0), (-1,-1), 'CENTER')]))
        story.append(grid_table)

    doc.build(story)
    
    cursor.execute("UPDATE viajes SET activo = 0 WHERE id = ?", (viaje_id,))
    conn.commit()
    conn.close()

    await update.message.reply_document(
        document=open(pdf_filename, 'rb'),
        caption=f"🧾 **Rendición Finalizada**\n**Total:** ${total_monto:.2f}"
    )

if __name__ == '__main__':
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    
    app.add_handler(CommandHandler("start", saludar_usuario))
    app.add_handler(MessageHandler(filters.Regex(r'^(?i)(hola|buenas|buen dia|inicio)'), saludar_usuario))
    app.add_handler(CommandHandler("iniciar_viaje", iniciar_viaje))
    app.add_handler(CommandHandler("gastos", listar_gastos))
    app.add_handler(CommandHandler("eliminar", eliminar_gasto))
    app.add_handler(CommandHandler("editar_monto", editar_monto))
    app.add_handler(CommandHandler("finalizar_viaje", finalizar_viaje))
    app.add_handler(MessageHandler(filters.PHOTO, procesar_gasto))

    app.run_polling()
