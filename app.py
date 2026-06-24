from flask import Flask, request, jsonify, send_from_directory
import os
import socket
import redis

app = Flask(__name__)

r = redis.Redis(host='redis-service', port=6379, decode_responses=True)

@app.route("/", methods=["GET"])
def home():
    return send_from_directory("/app", "index.html")

@app.route("/info", methods=["GET"])
def info():
    return jsonify({"pod": socket.gethostname()}), 200

@app.route("/pedidos", methods=["GET"])
def listar():
    raw = r.lrange('pedidos', 0, -1)
    pedidos = []
    for item in raw:
        partes = item.split("|")
        if len(partes) == 3:
            pedidos.append({
                "id": partes[0],
                "produto": partes[1],
                "quantidade": partes[2]
            })
    return jsonify(pedidos), 200

@app.route("/pedidos", methods=["POST"])
def adicionar():
    dados = request.json
    if not dados:
        return jsonify({"erro": "Envie JSON no body"}), 400

    produto = dados.get("produto", "").strip()
    qtd = dados.get("quantidade", 0)

    if not produto:
        return jsonify({"erro": "Nome do produto inválido"}), 400
    if not str(qtd).isdigit() or int(qtd) <= 0:
        return jsonify({"erro": "Quantidade deve ser um número maior que zero"}), 400

    raw = r.lrange('pedidos', 0, -1)

    for i, item in enumerate(raw):
        partes = item.split("|")
        if len(partes) == 3 and partes[1].lower() == produto.lower():
            nova_qtd = int(partes[2]) + int(qtd)
            r.lset('pedidos', i, f"{partes[0]}|{partes[1]}|{nova_qtd}")
            return jsonify({
                "mensagem": f"Produto '{produto}' já existia. Quantidade atualizada para {nova_qtd} itens."
            }), 200

    proximo_id = r.llen('pedidos') + 1
    r.rpush('pedidos', f"{proximo_id}|{produto}|{qtd}")
    return jsonify({
        "mensagem": f"Pedido #{proximo_id} registrado: {produto} ({qtd} itens)"
    }), 201

@app.route("/pedidos/<int:pedido_id>", methods=["DELETE"])
def deletar(pedido_id):
    raw = r.lrange('pedidos', 0, -1)
    for item in raw:
        partes = item.split("|")
        if len(partes) == 3 and int(partes[0]) == pedido_id:
            r.lrem('pedidos', 1, item)
            return jsonify({"mensagem": f"Pedido #{pedido_id} removido."}), 200
    return jsonify({"erro": "Pedido não encontrado."}), 404

@app.route("/pedidos", methods=["DELETE"])
def limpar():
    r.delete('pedidos')
    return jsonify({"mensagem": "Todos os pedidos foram removidos."}), 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
