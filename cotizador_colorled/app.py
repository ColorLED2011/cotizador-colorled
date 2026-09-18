import os
import re
import threading
import time
import xmlrpc.client
from datetime import datetime as _dt
from flask import Flask, request, jsonify, render_template

app = Flask(__name__)

# ── Credenciales Odoo (desde variables de entorno) ─────────────────
ODOO_URL  = os.environ.get("ODOO_URL",  "https://gfgroup.odoo.com")
ODOO_DB   = os.environ.get("ODOO_DB",   "gfgroup")
ODOO_USER = os.environ.get("ODOO_USER", "colorlednaguanagua@gmail.com")
ODOO_PASS = os.environ.get("ODOO_PASS", "GFgroup")

# ── Conexión XML-RPC ───────────────────────────────────────────────
def get_odoo():
    common = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/common")
    uid    = common.authenticate(ODOO_DB, ODOO_USER, ODOO_PASS, {})
    models = xmlrpc.client.ServerProxy(f"{ODOO_URL}/xmlrpc/2/object")
    return uid, models

def call(models, uid, model, method, args, kwargs=None):
    return models.execute_kw(
        ODOO_DB, uid, ODOO_PASS,
        model, method, args, kwargs or {}
    )


# ── Helper: genera tabla HTML de descuentos + marcador oculto ──────
def _generar_nota(subtotal, desc_divisas, desc_pronto, notas="", vendedor="", fecha=""):
    """
    Devuelve el campo 'note' completo con:
    - Marcador oculto <!-- CLDESC:d=X,p=X --> para recalculación posterior
    - Tabla HTML de descuentos con el subtotal correcto
    """
    total_final = subtotal
    filas_desc  = ""
    hay_divisas = False

    if desc_divisas:
        monto_div    = subtotal * 0.75
        total_final -= monto_div
        hay_divisas  = True
        filas_desc  += (
            f'<tr>'
            f'<td style="padding:1px 10px;border-bottom:1px solid #eee;color:#2e7d32;font-weight:500;white-space:nowrap;">Descuento 75% &mdash; Pago en divisas</td>'
            f'<td style="padding:1px 10px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;color:#c62828;font-weight:700;">&minus; USD {monto_div:,.2f}</td>'
            f'<td style="padding:1px 10px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;color:#1b5e20;font-weight:700;">'
            f'<span style="color:#888;font-weight:700;">Total desc. 75%:</span> USD {total_final:,.2f}</td>'
            f'</tr>'
        )

    if desc_pronto:
        monto_pp     = total_final * 0.10
        total_final -= monto_pp
        etiqueta_pp  = "Total desc. 75%+10%" if hay_divisas else "Total desc. 10%"
        filas_desc  += (
            f'<tr>'
            f'<td style="padding:1px 10px;border-bottom:1px solid #eee;color:#2e7d32;font-weight:500;white-space:nowrap;">Descuento 10% &mdash; Pronto pago 10 d&iacute;as</td>'
            f'<td style="padding:1px 10px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;color:#c62828;font-weight:700;">&minus; USD {monto_pp:,.2f}</td>'
            f'<td style="padding:1px 10px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;color:#1b5e20;font-weight:700;">'
            f'<span style="color:#888;font-weight:700;">{etiqueta_pp}:</span> USD {total_final:,.2f}</td>'
            f'</tr>'
        )

    # Marcador oculto con flags + subtotal — permite detectar cambios automáticamente
    marcador = f'<!-- CLDESC:d={1 if desc_divisas else 0},p={1 if desc_pronto else 0},s={subtotal:.2f} -->'

    if not filas_desc:
        return marcador + (f'<p style="font-size:13px;">{notas}</p>' if notas else "")

    # Pie de tabla con vendedor y fecha
    fecha_str = fecha or _dt.today().strftime("%-d/%-m/%Y")
    pie = f'{vendedor} &middot; COLOR LED &middot; {fecha_str}' if vendedor else f'COLOR LED &middot; {fecha_str}'

    tabla_html = (
        f'<table style="width:100%;border-collapse:collapse;font-size:14px;font-family:Arial;line-height:1.3;">'
        f'<tr style="background:#1a1a2e;color:#ffffff;">'
        f'<td colspan="3" style="padding:3px 10px;font-weight:bold;font-size:14px;letter-spacing:0.05em;">'
        f'Descuentos especiales aplicables</td></tr>'
        f'<tr>'
        f'<td colspan="2" style="padding:1px 10px;border-bottom:1px solid #eee;color:#555;font-weight:700;">Subtotal a precio lista (USD BASE)</td>'
        f'<td style="padding:1px 10px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;color:#555;">USD {subtotal:,.2f}</td>'
        f'</tr>'
        f'{filas_desc}'
        f'<tr><td colspan="3" style="padding:2px 10px;font-size:11px;color:#999;">'
        f'Vendedor: <strong>{pie}</strong></td></tr>'
        f'</table>'
    )
    if notas:
        tabla_html += f'<p style="margin-top:10px;font-size:13px;">{notas}</p>'

    return marcador + tabla_html


# ── Rutas ──────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/producto")
def buscar_producto():
    """Busca producto por referencia interna y devuelve el precio de la tarifa USD BASE."""
    codigo = request.args.get("codigo", "").strip().upper()
    if not codigo:
        return jsonify({"error": "Código requerido"}), 400
    try:
        uid, models = get_odoo()

        # 1. Buscar producto (con id de plantilla)
        resultados = call(
            models, uid,
            "product.product", "search_read",
            [[["default_code", "=", codigo]]],
            {"fields": ["id", "name", "default_code", "list_price", "product_tmpl_id", "image_128"], "limit": 1}
        )
        if not resultados:
            return jsonify({"error": "Código no encontrado"}), 404

        p = resultados[0]
        tmpl_id = p["product_tmpl_id"][0] if isinstance(p.get("product_tmpl_id"), list) else p.get("product_tmpl_id")
        precio = p["list_price"]

        # 2. Obtener tarifa USD BASE
        pricelists = call(
            models, uid, "product.pricelist", "search_read",
            [[["name", "ilike", "USD BASE"]]],
            {"fields": ["id"], "limit": 1}
        )

        if pricelists:
            pl_id = pricelists[0]["id"]
            campos = {"fields": ["compute_price", "fixed_price", "percent_price", "price_discount", "price_surcharge"], "limit": 1}

            # Buscar regla más específica: variante → plantilla → global
            for domain in [
                [["pricelist_id","=",pl_id], ["applied_on","=","0_product_variant"], ["product_id","=",p["id"]]],
                [["pricelist_id","=",pl_id], ["applied_on","=","1_product"],         ["product_tmpl_id","=",tmpl_id]],
                [["pricelist_id","=",pl_id], ["applied_on","=","3_global"]],
            ]:
                items = call(models, uid, "product.pricelist.item", "search_read", [domain], campos)
                if items:
                    item = items[0]
                    if item["compute_price"] == "fixed":
                        precio = item["fixed_price"]
                    elif item["compute_price"] == "percentage":
                        precio = p["list_price"] * (1 - item["percent_price"] / 100)
                    elif item["compute_price"] == "formula":
                        base = p["list_price"]
                        precio = (base - item.get("price_discount", 0)) * (1 - item.get("price_surcharge", 0) / 100)
                    break

        imagen = p.get("image_128")
        img_src = f"data:image/png;base64,{imagen}" if imagen else None

        return jsonify({
            "id":     p["id"],
            "codigo": p["default_code"],
            "nombre": p["name"],
            "precio": precio,
            "imagen": img_src
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/cliente")
def buscar_cliente():
    """Busca cliente por nombre o RIF."""
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify([])
    try:
        uid, models = get_odoo()
        domain = ["|", ["name", "ilike", q], ["vat", "ilike", q]]
        resultados = call(
            models, uid,
            "res.partner", "search_read",
            [domain],
            {"fields": ["id", "name", "vat"], "limit": 8}
        )
        return jsonify(resultados)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/pricelist")
def get_pricelist():
    """Devuelve el ID de la tarifa USD BCV."""
    try:
        uid, models = get_odoo()
        resultados = call(
            models, uid,
            "product.pricelist", "search_read",
            [[["name", "ilike", "BCV"]]],
            {"fields": ["id", "name"], "limit": 5}
        )
        return jsonify(resultados)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/enviar-pedido", methods=["POST"])
def enviar_pedido():
    """Crea el borrador de pedido en Odoo."""
    data = request.json

    vendedor      = data.get("vendedor", "")
    nombre_cliente= data.get("nombre_cliente", "")
    rif_cliente   = data.get("rif_cliente", "")
    lineas        = data.get("lineas", [])
    desc_divisas  = data.get("desc_divisas", False)
    desc_pronto   = data.get("desc_pronto", False)
    notas         = data.get("notas", "")

    if not lineas:
        return jsonify({"error": "El pedido no tiene productos"}), 400

    try:
        uid, models = get_odoo()

        # 1. Buscar o crear partner
        partner_ids = call(
            models, uid, "res.partner", "search",
            [[["name", "ilike", nombre_cliente]]], {"limit": 1}
        )
        if partner_ids:
            partner_id = partner_ids[0]
        else:
            partner_id = call(
                models, uid, "res.partner", "create",
                [{"name": nombre_cliente, "vat": rif_cliente, "customer_rank": 1}]
            )

        # 2. Buscar tarifa USD BCV
        pricelists = call(
            models, uid, "product.pricelist", "search_read",
            [[["name", "ilike", "USD BASE"]]],
            {"fields": ["id"], "limit": 1}
        )
        pricelist_id = pricelists[0]["id"] if pricelists else False

        # 3. Armar líneas del pedido (sin descuento en línea — se informa en nota)
        order_lines = []
        subtotal = 0.0
        for linea in lineas:
            product_ids = call(
                models, uid, "product.product", "search",
                [[["default_code", "=", linea["codigo"]]]], {"limit": 1}
            )
            if not product_ids:
                return jsonify({"error": f"Producto no encontrado: {linea['codigo']}"}), 400

            monto_linea = linea["cantidad"] * linea["precio"]
            subtotal   += monto_linea

            order_lines.append((0, 0, {
                "product_id":      product_ids[0],
                "name":            linea["descripcion"],
                "product_uom_qty": linea["cantidad"],
                "price_unit":      linea["precio"],
            }))

        # 4. Calcular descuentos e incluirlos como tabla HTML en la nota
        nota_completa = _generar_nota(subtotal, desc_divisas, desc_pronto, notas, vendedor)

        # 6. Crear pedido
        order_vals = {
            "partner_id":       partner_id,
            "client_order_ref": vendedor,
            "order_line":       order_lines,
            "note":             nota_completa,
        }
        if pricelist_id:
            order_vals["pricelist_id"] = pricelist_id

        order_id = call(models, uid, "sale.order", "create", [order_vals])

        # 7. Leer la referencia generada
        order_data = call(
            models, uid, "sale.order", "read",
            [[order_id]], {"fields": ["name"]}
        )
        order_name = order_data[0]["name"] if order_data else str(order_id)

        return jsonify({"ok": True, "referencia": order_name, "id": order_id})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ficha/<codigo>")
def ficha_tecnica(codigo):
    """Devuelve ficha técnica completa de un producto: descripción, atributos, imagen grande y precio USD BASE."""
    try:
        uid, models = get_odoo()

        # 1. Buscar producto
        resultados = call(models, uid, "product.product", "search_read",
            [[["default_code", "=", codigo.upper()]]],
            {"fields": ["id", "name", "default_code", "list_price", "product_tmpl_id",
                        "description_sale", "categ_id", "image_512"], "limit": 1})
        if not resultados:
            return jsonify({"error": "Producto no encontrado"}), 404

        p       = resultados[0]
        tmpl_id = p["product_tmpl_id"][0] if isinstance(p.get("product_tmpl_id"), list) else p.get("product_tmpl_id")

        # 2. Precio USD BASE (misma lógica que /api/producto)
        precio_base = p["list_price"]
        pricelists  = call(models, uid, "product.pricelist", "search_read",
            [[["name", "ilike", "USD BASE"]]], {"fields": ["id"], "limit": 1})
        if pricelists:
            pl_id  = pricelists[0]["id"]
            campos = {"fields": ["compute_price", "fixed_price", "percent_price",
                                 "price_discount", "price_surcharge"], "limit": 1}
            for domain in [
                [["pricelist_id","=",pl_id],["applied_on","=","0_product_variant"],["product_id","=",p["id"]]],
                [["pricelist_id","=",pl_id],["applied_on","=","1_product"],["product_tmpl_id","=",tmpl_id]],
                [["pricelist_id","=",pl_id],["applied_on","=","3_global"]],
            ]:
                items = call(models, uid, "product.pricelist.item", "search_read", [domain], campos)
                if items:
                    item = items[0]
                    if item["compute_price"] == "fixed":
                        precio_base = item["fixed_price"]
                    elif item["compute_price"] == "percentage":
                        precio_base = p["list_price"] * (1 - item["percent_price"] / 100)
                    elif item["compute_price"] == "formula":
                        precio_base = (p["list_price"] - item.get("price_discount", 0)) * (1 - item.get("price_surcharge", 0) / 100)
                    break

        # 3. Atributos / especificaciones técnicas
        attr_lines = call(models, uid, "product.template.attribute.line", "search_read",
            [[["product_tmpl_id", "=", tmpl_id]]],
            {"fields": ["attribute_id", "value_ids"]})

        specs = []
        for line in attr_lines:
            attr_name = line["attribute_id"][1] if isinstance(line["attribute_id"], list) else str(line["attribute_id"])
            if line["value_ids"]:
                values    = call(models, uid, "product.attribute.value", "read",
                    [line["value_ids"]], {"fields": ["name"]})
                attr_val  = ", ".join(v["name"] for v in values)
                specs.append({"atributo": attr_name, "valor": attr_val})

        # 4. Categoría e imagen
        categ        = p.get("categ_id")
        categ_nombre = categ[1] if isinstance(categ, list) else ""
        if "/" in categ_nombre:
            categ_nombre = categ_nombre.split("/")[-1].strip()

        imagen  = p.get("image_512")
        img_src = f"data:image/png;base64,{imagen}" if imagen else None

        return jsonify({
            "codigo":      p["default_code"],
            "nombre":      p["name"],
            "categoria":   categ_nombre,
            "descripcion": p.get("description_sale") or "",
            "imagen":      img_src,
            "precio_base": precio_base,
            "specs":       specs,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/catalogo")
def catalogo():
    """Devuelve todos los productos activos con precios USD BASE y descuentos."""
    try:
        uid, models = get_odoo()

        # 1. Buscar tarifa USD BASE
        pricelists = call(models, uid, "product.pricelist", "search_read",
            [[["name", "ilike", "USD BASE"]]], {"fields": ["id"], "limit": 1})
        pl_id = pricelists[0]["id"] if pricelists else None

        # 2. Obtener TODOS los items de la tarifa en una sola llamada
        pl_by_variant  = {}
        pl_by_template = {}
        pl_global      = None

        if pl_id:
            items = call(models, uid, "product.pricelist.item", "search_read",
                [[["pricelist_id", "=", pl_id]]],
                {"fields": ["applied_on", "product_id", "product_tmpl_id",
                            "compute_price", "fixed_price", "percent_price",
                            "price_discount", "price_surcharge"]})
            for item in items:
                if item["applied_on"] == "0_product_variant" and item["product_id"]:
                    pid = item["product_id"][0] if isinstance(item["product_id"], list) else item["product_id"]
                    pl_by_variant[pid] = item
                elif item["applied_on"] == "1_product" and item["product_tmpl_id"]:
                    tid = item["product_tmpl_id"][0] if isinstance(item["product_tmpl_id"], list) else item["product_tmpl_id"]
                    pl_by_template[tid] = item
                elif item["applied_on"] == "3_global":
                    pl_global = item

        # 3. Obtener productos activos con código y existencia > 0
        productos = call(models, uid, "product.product", "search_read",
            [[["active", "=", True], ["default_code", "!=", False],
              ["sale_ok", "=", True], ["qty_available", ">", 0]]],
            {"fields": ["id", "name", "default_code", "list_price", "product_tmpl_id", "image_128", "qty_available"],
             "order": "default_code asc"})

        # 4. Calcular precio USD BASE por producto (cascada variante → plantilla → global)
        resultado = []
        for p in productos:
            tmpl_id     = p["product_tmpl_id"][0] if isinstance(p.get("product_tmpl_id"), list) else p.get("product_tmpl_id")
            precio_lista = p["list_price"]
            precio_base  = precio_lista  # fallback

            if p["id"] in pl_by_variant:
                item = pl_by_variant[p["id"]]
            elif tmpl_id in pl_by_template:
                item = pl_by_template[tmpl_id]
            elif pl_global:
                item = pl_global
            else:
                item = None

            if item:
                if item["compute_price"] == "fixed":
                    precio_base = item["fixed_price"]
                elif item["compute_price"] == "percentage":
                    precio_base = precio_lista * (1 - item["percent_price"] / 100)
                elif item["compute_price"] == "formula":
                    precio_base = (precio_lista - item.get("price_discount", 0)) * (1 - item.get("price_surcharge", 0) / 100)

            imagen = p.get("image_128")
            img_src = f"data:image/png;base64,{imagen}" if imagen else None

            resultado.append({
                "id":           p["id"],
                "codigo":       p["default_code"],
                "nombre":       p["name"],
                "precio_lista": precio_lista,
                "precio_base":  precio_base,
                "imagen":       img_src,
            })

        return jsonify(resultado)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/recalcular/<int:order_id>", methods=["POST"])
def recalcular_nota(order_id):
    """
    Recalcula la tabla de descuentos usando el subtotal REAL del pedido en Odoo.
    Llamar después de que alguien modifique productos en Odoo (antes de confirmar).
    """
    try:
        uid, models = get_odoo()

        # 1. Leer pedido actual de Odoo
        data = call(models, uid, "sale.order", "read",
            [[order_id]],
            {"fields": ["name", "amount_untaxed", "note", "client_order_ref", "date_order"]}
        )
        if not data:
            return jsonify({"error": "Pedido no encontrado"}), 404
        order = data[0]

        subtotal   = float(order.get("amount_untaxed") or 0)
        note_vieja = order.get("note") or ""
        vendedor   = order.get("client_order_ref") or ""

        # Fecha original del pedido (mantener la fecha de creación, no hoy)
        date_raw = order.get("date_order") or ""
        fecha = ""
        if date_raw and date_raw is not False:
            try:
                d = str(date_raw)[:10].split("-")   # "2026-09-11" → ["2026","09","11"]
                fecha = f"{int(d[2])}/{int(d[1])}/{d[0]}"  # "11/9/2026"
            except Exception:
                fecha = ""

        # 2. Extraer flags del marcador oculto <!-- CLDESC:d=X,p=X[,s=SUBTOTAL] -->
        match = re.search(r'<!-- CLDESC:d=(\d),p=(\d)(?:,s=([\d.]+))? -->', note_vieja)
        if match:
            desc_divisas = match.group(1) == "1"
            desc_pronto  = match.group(2) == "1"
        else:
            # Fallback para pedidos antiguos sin marcador: detectar por contenido
            desc_divisas = "Pago en divisas" in note_vieja or "75%" in note_vieja
            desc_pronto  = "Pronto pago" in note_vieja or "10%" in note_vieja

        # 3. Extraer notas de texto libre (fuera de la tabla HTML)
        notas_extra = ""
        p_match = re.search(r'<p[^>]*>(.*?)</p>', note_vieja, re.DOTALL)
        if p_match:
            notas_extra = p_match.group(1).strip()

        # 4. Generar nota nueva con subtotal correcto
        nueva_nota = _generar_nota(subtotal, desc_divisas, desc_pronto, notas_extra, vendedor, fecha)

        # 5. Actualizar en Odoo
        call(models, uid, "sale.order", "write", [[order_id], {"note": nueva_nota}])

        return jsonify({
            "ok":             True,
            "referencia":     order["name"],
            "subtotal_nuevo": subtotal,
            "desc_divisas":   desc_divisas,
            "desc_pronto":    desc_pronto,
        })

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ── Recalculación automática en background ─────────────────────────
import logging as _log

def _recalcular_pedidos_pendientes():
    """
    Busca pedidos con marcador CLDESC cuyo subtotal real en Odoo difiere
    del subtotal guardado en el marcador, y actualiza la nota automáticamente.
    Se ejecuta cada 2 minutos desde un hilo daemon.
    """
    try:
        uid, models = get_odoo()

        # Solo pedidos activos (no cancelados/completados) que tengan marcador CLDESC
        pedidos = call(models, uid, "sale.order", "search_read",
            [[["state", "not in", ["done", "cancel"]], ["note", "like", "CLDESC"]]],
            {"fields": ["id", "name", "amount_untaxed", "note",
                        "client_order_ref", "date_order"],
             "limit": 50, "order": "write_date desc"}
        )

        actualizados = 0
        for order in pedidos:
            subtotal   = float(order.get("amount_untaxed") or 0)
            note_vieja = order.get("note") or ""

            # Extraer flags y subtotal guardado del marcador
            m = re.search(r'<!-- CLDESC:d=(\d),p=(\d)(?:,s=([\d.]+))? -->', note_vieja)
            if not m:
                continue

            # Comparar subtotal actual vs guardado — solo actualizar si cambió
            s_guardado = float(m.group(3)) if m.group(3) else None
            if s_guardado is not None and abs(subtotal - s_guardado) < 0.01:
                continue   # Sin cambios, no tocar

            desc_divisas = m.group(1) == "1"
            desc_pronto  = m.group(2) == "1"
            vendedor     = order.get("client_order_ref") or ""

            # Preservar fecha original del pedido
            date_raw = order.get("date_order") or ""
            fecha = ""
            if date_raw:
                try:
                    d = str(date_raw)[:10].split("-")
                    fecha = f"{int(d[2])}/{int(d[1])}/{d[0]}"
                except Exception:
                    pass

            # Extraer notas de texto libre
            notas_extra = ""
            p_m = re.search(r'<p[^>]*>(.*?)</p>', note_vieja, re.DOTALL)
            if p_m:
                notas_extra = p_m.group(1).strip()

            nueva_nota = _generar_nota(subtotal, desc_divisas, desc_pronto,
                                        notas_extra, vendedor, fecha)
            call(models, uid, "sale.order", "write",
                 [[order["id"]], {"note": nueva_nota}])
            actualizados += 1
            _log.getLogger(__name__).info(
                f"[CLDESC-AUTO] {order['name']}: subtotal {s_guardado} → {subtotal}"
            )

        return actualizados

    except Exception as e:
        _log.getLogger(__name__).error(f"[CLDESC-AUTO] Error: {e}")
        return 0


def _hilo_recalcular():
    """Hilo daemon: recalcula notas de descuento automáticamente cada 2 minutos."""
    time.sleep(20)   # Breve pausa al arranque para que la app esté lista
    while True:
        _recalcular_pedidos_pendientes()
        time.sleep(120)   # Repetir cada 2 minutos


# Iniciar hilo en background al cargar el módulo (funciona con gunicorm)
threading.Thread(target=_hilo_recalcular, daemon=True, name="cldesc-auto").start()


if __name__ == "__main__":
    app.run(debug=True, port=5000)
