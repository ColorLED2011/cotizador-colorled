import os
import xmlrpc.client
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
        total_final  = subtotal
        filas_desc   = ""

        if desc_divisas:
            monto_div    = subtotal * 0.75
            total_final -= monto_div
            filas_desc  += (
                f'<tr style="color:#2e7d32;">'
                f'<td style="padding:7px 12px;border-bottom:1px solid #eee;">Descuento 75% — Pago en divisas</td>'
                f'<td style="padding:7px 12px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;">− USD {monto_div:,.2f}</td>'
                f'</tr>'
            )
        if desc_pronto:
            monto_pp     = total_final * 0.10
            total_final -= monto_pp
            filas_desc  += (
                f'<tr style="color:#2e7d32;">'
                f'<td style="padding:7px 12px;border-bottom:1px solid #eee;">Descuento 10% — Pronto pago 10 días</td>'
                f'<td style="padding:7px 12px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;">− USD {monto_pp:,.2f}</td>'
                f'</tr>'
            )

        nota_completa = notas
        if filas_desc:
            tabla_html = (
                f'<table style="width:100%;border-collapse:collapse;font-size:13px;font-family:Arial;">'
                f'<tr style="background:#1a1a2e;color:#ffffff;">'
                f'<td colspan="2" style="padding:8px 12px;font-weight:bold;font-size:12px;letter-spacing:0.04em;">'
                f'🏷 Descuentos especiales aplicables</td></tr>'
                f'<tr><td style="padding:7px 12px;border-bottom:1px solid #eee;color:#444;">Subtotal a precio lista (USD BASE)</td>'
                f'<td style="padding:7px 12px;border-bottom:1px solid #eee;text-align:right;white-space:nowrap;color:#444;">USD {subtotal:,.2f}</td></tr>'
                f'{filas_desc}'
                f'<tr style="background:#f1f8e9;">'
                f'<td style="padding:9px 12px;font-weight:bold;color:#1b5e20;font-size:14px;">✔ Total a pagar con descuentos</td>'
                f'<td style="padding:9px 12px;font-weight:bold;color:#1b5e20;font-size:14px;text-align:right;white-space:nowrap;">USD {total_final:,.2f}</td>'
                f'</tr></table>'
            )
            if notas:
                tabla_html += f'<p style="margin-top:10px;font-size:13px;">{notas}</p>'
            nota_completa = tabla_html

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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
