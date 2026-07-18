from flask import Flask, request, jsonify, send_from_directory
import os
import socket

import redis
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

app = Flask(__name__)

# ---------------------------------------------------------------
# PostgreSQL — fonte de verdade dos pedidos
# Credenciais SEMPRE via variáveis de ambiente (nunca no código).
# Defaults apontam para o ambiente de desenvolvimento local.
# ---------------------------------------------------------------
DB_USER = os.getenv("DB_USER", "ecommerce")
DB_PASSWORD = os.getenv("DB_PASSWORD", "ecommerce")
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "ecommerce")

app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"postgresql+psycopg://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class Pedido(db.Model):
    __tablename__ = "pedidos"

    id = db.Column(db.Integer, primary_key=True)
    produto = db.Column(db.String(120), nullable=False)
    quantidade = db.Column(db.Integer, nullable=False)

    def to_dict(self):
        return {
            "id": self.id,
            "produto": self.produto,
            "quantidade": self.quantidade,
        }


# ---------------------------------------------------------------
# Redis — contador de hits por pod (demonstra o load balancing).
# Se o Redis estiver indisponível, o app segue funcionando:
# só perde o contador, nunca os pedidos.
# ---------------------------------------------------------------
REDIS_HOST = os.getenv("REDIS_HOST", "redis-service")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
POD_NAME = socket.gethostname()


def conectar_redis():
    try:
        cliente = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
            decode_responses=True,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        cliente.ping()
        return cliente
    except redis.RedisError:
        return None


r = conectar_redis()


def registrar_hit():
    global r
    if r is None:
        r = conectar_redis()
    if r is None:
        return None
    try:
        return r.incr(f"hits:{POD_NAME}")
    except redis.RedisError:
        return None


@app.before_request
def contar_requisicao():
    registrar_hit()


# ---------------------------------------------------------------
# Rotas
# ---------------------------------------------------------------
@app.route("/", methods=["GET"])
def home():
    return send_from_directory("/app", "index.html")


@app.route("/info", methods=["GET"])
def info():
    resposta = {"pod": POD_NAME, "redis": r is not None}
    if r is not None:
        try:
            chaves = sorted(r.keys("hits:*"))
            resposta["hits"] = {
                chave.replace("hits:", ""): int(r.get(chave) or 0)
                for chave in chaves
            }
        except redis.RedisError:
            resposta["redis"] = False
    return jsonify(resposta), 200


@app.route("/pedidos", methods=["GET"])
def listar():
    pedidos = Pedido.query.order_by(Pedido.id).all()
    return jsonify([p.to_dict() for p in pedidos]), 200


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

    existente = Pedido.query.filter(
        func.lower(Pedido.produto) == produto.lower()
    ).first()

    if existente:
        existente.quantidade += int(qtd)
        db.session.commit()
        return jsonify({
            "mensagem": (
                f"Produto '{produto}' já existia. "
                f"Quantidade atualizada para {existente.quantidade} itens."
            )
        }), 200

    novo = Pedido(produto=produto, quantidade=int(qtd))
    db.session.add(novo)
    db.session.commit()
    return jsonify({
        "mensagem": f"Pedido #{novo.id} registrado: {produto} ({qtd} itens)"
    }), 201


@app.route("/pedidos/<int:pedido_id>", methods=["DELETE"])
def deletar(pedido_id):
    pedido = db.session.get(Pedido, pedido_id)
    if pedido is None:
        return jsonify({"erro": "Pedido não encontrado."}), 404
    db.session.delete(pedido)
    db.session.commit()
    return jsonify({"mensagem": f"Pedido #{pedido_id} removido."}), 200


@app.route("/pedidos", methods=["DELETE"])
def limpar():
    Pedido.query.delete()
    db.session.commit()
    return jsonify({"mensagem": "Todos os pedidos foram removidos."}), 200

@app.route("/health", methods=["GET"])
def health():
    # Health check simples para as probes do Kubernetes.
    # Nao consulta banco nem Redis de proposito: liveness deve
    # refletir "o processo esta vivo", nao "as dependencias estao
    # ok" — senao um banco caido reiniciaria pods sadios em loop.
    return jsonify({"status": "UP"}), 200

with app.app_context():
    db.create_all()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
