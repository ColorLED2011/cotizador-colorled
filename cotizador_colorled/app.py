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

        # Buscar producto
        resultados = call(
            models, uid,
            "product.product", "search_read",
            [[["default_code", "=", codigo]]],
            {"fields": ["id", "name", "default_code", "list_price"], "limit": 1}
        )
        if not resultados:
            return jsonify({"error": "Código no encontrado"}), 404

        p = resultados[0]

        # Obtener precio de la tarifa USD BASE
        pricelists = call(
            models, uid, "product.pricelist", "search_read",
            [[["name", "ilike", "USD BASE"]]],
            {"fields": ["id"], "limit": 1}
        )
        precio = p["list_price"]
        if pricelists:
            pricelist_id = pricelists[0]["id"]
            try:
                precio = models.execute_kw(
                    ODOO_DB, uid, ODOO_PASS,
                    "product.pricelist", "get_product_price",
                    [pricelist_id, p["id"], 1.0, False]
                )
            except Exception:
                precio = p["list_price"]

        return jsonify({
            "id":     p["id"],
            "codigo": p["default_code"],
            "nombre": p["name"],
            "precio": precio
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

        # 3. Calcular descuento combinado por línea
        desc_pct = 0.0
        notas_desc = []
        if desc_divisas:
            desc_pct  = 75.0
            notas_desc.append("Descuento 75% pago divisas")
        if desc_pronto:
            # Se aplica sobre el precio ya descontado (cascada)
            desc_pct  = 1 - (1 - desc_pct / 100) * (1 - 0.10)
            desc_pct  = round(desc_pct * 100, 4)
            notas_desc.append("Descuento 10% pronto pago 10 días")

        # 4. Armar líneas del pedido
        order_lines = []
        for linea in lineas:
            product_ids = call(
                models, uid, "product.product", "search",
                [[["default_code", "=", linea["codigo"]]]], {"limit": 1}
            )
            if not product_ids:
                return jsonify({"error": f"Producto no encontrado: {linea['codigo']}"}), 400

            order_lines.append((0, 0, {
                "product_id":       product_ids[0],
                "name":             linea["descripcion"],
                "product_uom_qty":  linea["cantidad"],
                "price_unit":       linea["precio"],
                "discount":         desc_pct,
            }))

        # 5. Notas finales
        nota_completa = notas
        if notas_desc:
            nota_completa = "\n".join(notas_desc) + ("\n\n" + notas if notas else "")

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


if __name__ == "__main__":
    app.run(debug=True, port=5000)
