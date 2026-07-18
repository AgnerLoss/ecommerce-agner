# Manifests Kubernetes — ecommerce-agner

Estrutura de deploy do e-commerce na Magalu Cloud (Movetech · grande final).

## Arquivos (aplicar nesta ordem)

| Arquivo | O que cria |
|---|---|
| `01-secret.yaml` | Secret com credenciais do DBaaS (PostgreSQL) |
| `02-redis.yaml` | Redis (Deployment + Service) — contador de hits por pod |
| `03-deployment.yaml` | App Flask/gunicorn v4.0 (3 réplicas, probes, resources) |
| `04-service.yaml` | Service LoadBalancer — ponto de entrada externo |
| `05-hpa.yaml` | HorizontalPodAutoscaler (3→10 pods, alvo 50% CPU) |

> **Antes de tudo:** adicionar o endpoint `/health` no `app.py`
> (ver `PATCH-health-endpoint.txt`) e rebuildar a imagem, senão as
> probes falham.

---

## Teste local no kind (validação antes de subir na Magalu)

### 1. Criar o cluster kind
```bash
kind create cluster --name ecommerce
kubectl cluster-info --context kind-ecommerce
```

### 2. Instalar o metrics-server (o HPA precisa dele)
```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# No kind, o metrics-server precisa ignorar TLS do kubelet.
# Patch necessário só em ambiente local:
kubectl patch -n kube-system deployment metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

### 3. Subir um Postgres de teste no cluster (substitui o DBaaS localmente)
> No kind não temos o DBaaS da Magalu. Para validar, sobe-se um
> Postgres simples dentro do cluster. Os valores batem com o Secret.
```bash
kubectl create deployment pg --image=postgres:16-alpine
kubectl set env deployment/pg \
  POSTGRES_USER=ecommerce POSTGRES_PASSWORD=ecommerce POSTGRES_DB=ecommerce
kubectl expose deployment pg --name=pg-service --port=5432
```

### 4. Ajustar o Secret para o teste local
Edite `01-secret.yaml` e troque os placeholders por:
```yaml
  DB_USER: "ecommerce"
  DB_PASSWORD: "ecommerce"
  DB_HOST: "pg-service"
  DB_PORT: "5432"
  DB_NAME: "ecommerce"
```

### 5. Carregar a imagem local no kind
> No teste local usamos a imagem buildada na máquina (não a do
> registry da Magalu). Rebuild com o `/health` já adicionado:
```bash
docker build -t ecommerce-web:local ..
kind load docker-image ecommerce-web:local --name ecommerce
```
E no `03-deployment.yaml`, para o teste local:
- troque a linha `image:` por `image: ecommerce-web:local`
- comente o bloco `imagePullSecrets` (não precisa no kind)

### 6. Aplicar tudo
```bash
kubectl apply -f 01-secret.yaml
kubectl apply -f 02-redis.yaml
kubectl apply -f 03-deployment.yaml
kubectl apply -f 04-service.yaml
kubectl apply -f 05-hpa.yaml
```

### 7. Verificar
```bash
kubectl get pods          # 3 pods do app + 1 redis + 1 pg, todos Running/Ready
kubectl get hpa           # deve mostrar métrica de CPU (não <unknown>)
kubectl get svc           # ecommerce-service fica <pending> no kind (normal)
```

### 8. Acessar (port-forward, já que LB fica pending no kind)
```bash
kubectl port-forward svc/ecommerce-service 8080:80
# noutro terminal:
curl http://localhost:8080/health     # {"status":"UP"}
curl http://localhost:8080/info       # mostra o pod que respondeu
curl http://localhost:8080/pedidos    # lista pedidos (do Postgres)
```

### 9. Testar auto-cura (o clássico)
```bash
kubectl delete pod <um-dos-pods-do-ecommerce>
kubectl get pods -w      # um novo nasce sozinho pra manter 3 réplicas
```

### 10. Limpeza
```bash
kind delete cluster --name ecommerce
```

---

## Diferenças local (kind) × produção (Magalu MKE)

| Item | kind (local) | Magalu MKE (grande final) |
|---|---|---|
| Imagem | `ecommerce-web:local` | imagem do Container Registry |
| imagePullSecrets | comentado | ativo (`magalu-registry-secret`) |
| Banco | Postgres no cluster | DBaaS PostgreSQL (host interno) |
| Service LB | fica `<pending>` → port-forward | IP externo real |
| metrics-server | instalar + `--kubelet-insecure-tls` | geralmente já presente |
