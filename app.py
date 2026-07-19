from flask import Flask, request, jsonify, send_from_directory
import os
import socket
import uuid

import redis
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.utils import secure_filename

from urllib.parse import quote_plus

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
    f"postgresql+psycopg://{DB_USER}:{quote_plus(DB_PASSWORD)}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class Pedido(db.Model):
    __tablename__ = "pedidos"

    id = db.Column(db.Integer, primary_key=True)
    produto = db.Column(db.String(120), nullable=False)
    quantidade = db.Column(db.Integer, nullable=False)
    # URL/caminho da imagem do produto. Nullable: produto pode
    # existir sem foto. O banco guarda só a referência (URL),
    # NUNCA o binário da imagem — arquivo vai para o storage.
    imagem_url = db.Column(db.String(255), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "produto": self.produto,
            "quantidade": self.quantidade,
            "imagem_url": self.imagem_url,
        }


# ---------------------------------------------------------------
# Camada de storage — ISOLADA de propósito.
# Hoje: salva o arquivo em disco local (dev/kind).
# Amanhã: trocar SÓ o corpo de salvar_imagem() para enviar ao
# Object Storage (boto3/S3) — nenhuma outra parte do app muda.
# É a mesma lógica do "primeiro local, depois nuvem".
# ---------------------------------------------------------------
UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/app/uploads")
EXTENSOES_OK = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_BYTES = 5 * 1024 * 1024  # 5 MB

os.makedirs(UPLOAD_DIR, exist_ok=True)


def extensao_permitida(nome_arquivo):
    return (
        "." in nome_arquivo
        and nome_arquivo.rsplit(".", 1)[1].lower() in EXTENSOES_OK
    )


def salvar_imagem(arquivo):
    """
    Recebe um FileStorage do Flask, salva no backend de storage
    atual e devolve a URL pública do arquivo.

    >>> TROCA FUTURA PARA OBJECT STORAGE ACONTECE AQUI DENTRO <<<
    O resto do app (rota, model, front) não sabe nem se importa
    onde o arquivo foi parar — só recebe uma URL de volta.
    """
    # Nome único para não sobrescrever arquivos de mesmo nome.
    ext = arquivo.filename.rsplit(".", 1)[1].lower()
    nome_seguro = secure_filename(f"{uuid.uuid4().hex}.{ext}")

    destino = os.path.join(UPLOAD_DIR, nome_seguro)
    arquivo.save(destino)

    # URL que o browser usa para buscar a imagem. Local: servida
    # pela própria app em /uploads/<arquivo>. No Object Storage,
    # aqui retornaria a URL pública/CDN do objeto.
    return f"/uploads/{nome_seguro}"


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


@app.route("/uploads/<path:nome>", methods=["GET"])
def servir_upload(nome):
    # Serve as imagens salvas localmente (dev/kind). No Object
    # Storage esta rota deixa de ser usada: a URL aponta direto
    # para o bucket/CDN, sem passar pela aplicação.
    return send_from_directory(UPLOAD_DIR, nome)


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
    # Aceita tanto JSON puro (sem foto) quanto multipart/form-data
    # (com foto), já que upload de arquivo exige multipart.
    if request.content_type and request.content_type.startswith(
        "multipart/form-data"
    ):
        produto = (request.form.get("produto") or "").strip()
        qtd = request.form.get("quantidade", 0)
        arquivo = request.files.get("imagem")
    else:
        dados = request.json or {}
        produto = (dados.get("produto") or "").strip()
        qtd = dados.get("quantidade", 0)
        arquivo = None

    if not produto:
        return jsonify({"erro": "Nome do produto inválido"}), 400
    if not str(qtd).isdigit() or int(qtd) <= 0:
        return jsonify({"erro": "Quantidade deve ser um número maior que zero"}), 400

    # Validação e upload da imagem (se veio uma).
    imagem_url = None
    if arquivo and arquivo.filename:
        if not extensao_permitida(arquivo.filename):
            return jsonify({
                "erro": "Formato inválido. Use png, jpg, jpeg, gif ou webp."
            }), 400

        arquivo.seek(0, os.SEEK_END)
        tamanho = arquivo.tell()
        arquivo.seek(0)
        if tamanho > MAX_BYTES:
            return jsonify({"erro": "Imagem muito grande (máx. 5 MB)."}), 400

        imagem_url = salvar_imagem(arquivo)

    existente = Pedido.query.filter(
        func.lower(Pedido.produto) == produto.lower()
    ).first()

    if existente:
        existente.quantidade += int(qtd)
        # Se veio foto nova, atualiza; senão mantém a que já tinha.
        if imagem_url:
            existente.imagem_url = imagem_url
        db.session.commit()
        return jsonify({
            "mensagem": (
                f"Produto '{produto}' já existia. "
                f"Quantidade atualizada para {existente.quantidade} itens."
            )
        }), 200

    novo = Pedido(produto=produto, quantidade=int(qtd), imagem_url=imagem_url)
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
