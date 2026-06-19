# Skill: remote-workspace

Acceso a **tu Claude personal + tus proyectos** desde una laptop corporativa
bloqueada por **Zscaler**, usando **solo el browser** del lado del cliente.

> **Frontera (por diseño):** este es TU workspace y TU disco personal. No metas
> código del empleador acá. Del lado de la Mac corporativa no se instala nada —
> solo se visita una URL HTTPS servida por Cloudflare. Igual: usar tu cuenta
> personal desde un equipo corporativo puede ir contra la política de tu empresa;
> eso lo decidís vos.

## Estado — handoff (2026-06-18)

**HECHO (commiteado en `37e3996af`, WIP bundleado):**
- Rol `install-remote-workspace` completo: code-server (:8443) + rclone-webdav
  sidecar (:8080) compartiendo PVC longhorn-nvme + cloudflared (x2). En
  `playbooks/bootstrap.yml` con tag `remote-workspace`. Docs en `CLAUDE.md` y
  `README.md`. `secrets.yml` con placeholders (gitignored).
- **Pre-flight validado contra el cluster real:** kubectl OK; prerequisitos
  confirmados (`longhorn-nvme`, `cluster-gateway` programmed en .200, CRD
  HTTPRoute); `kubectl apply --dry-run=server` de los 6 objetos pasa limpio;
  namespace `workspace` ya creado. → el deploy debería andar a la primera apenas
  haya secrets reales.

**FALTA (todo depende de tu cuenta Cloudflare — ver pasos 1-5 abajo):**
1. [ ] Crear Tunnel en Cloudflare → copiar token
2. [ ] Public Hostnames: `claude.*→:8443`, `files.*→:8080`
3. [ ] Cloudflare Access: app `claude.*` (email OTP) + app `files.*` (Service Token)
4. [ ] Llenar `roles/install-remote-workspace/defaults/secrets.yml`
5. [ ] `ansible-playbook ... --tags remote-workspace` + instalar `claude` dentro

## Arquitectura

```
[Mac corporativa + Zscaler]  ── solo browser, HTTPS :443 ──►  [Cloudflare edge]
                                                                     │ túnel saliente
                                                                     ▼
                                                          [K3s homelab]
                                                          namespace: workspace
                                                          ├─ Pod remote-workspace
                                                          │   ├─ code-server (:8443)  → terminal `claude` + editor
                                                          │   └─ rclone webdav (:8080) → mismos archivos
                                                          │   PVC longhorn-nvme compartido
                                                          └─ cloudflared (x2) → túnel a Cloudflare
```

- **code-server** y **webdav** son 2 containers del mismo pod → comparten el PVC
  `remote-workspace-data` (sin multi-attach: misma pod, mismo nodo).
- **cloudflared** sale por 443 a Cloudflare (sin abrir puertos en casa). Zscaler
  ve HTTPS a Cloudflare = categoría confiable.

## Rol / tags

- Rol: `install-remote-workspace` · Tag: `remote-workspace`
- Deps: `networking` + `longhorn` (PVC). LiteLLM/AI NO requeridos.
- Defaults: `roles/install-remote-workspace/defaults/main.yml`
- Secrets (gitignored): `roles/install-remote-workspace/defaults/secrets.yml`

## Setup — orden exacto

### 1. Cloudflare (una sola vez, en tu cuenta personal)
1. Tené un dominio en Cloudflare (gratis con cualquier dominio apuntando sus NS).
2. Zero Trust → **Networks → Tunnels → Create a tunnel → Cloudflared**.
3. Copiá el **token** del comando de instalación (la cadena larga después de
   `--token`). Va en `secrets.yml` como `remote_workspace_cloudflared_token`.
4. En el tunnel, **Public Hostnames** → agregá dos:
   | Hostname | Service |
   |---|---|
   | `claude.tudominio.com` | `http://remote-workspace.workspace:8443` |
   | `files.tudominio.com`  | `http://remote-workspace.workspace:8080` |
5. **Protegé ambos con Cloudflare Access** (Zero Trust → Access → Applications):
   policy de email OTP a tu mail, o Google OAuth, o mTLS/client-cert si querés
   algo tipo "llave". Sin esto, cualquiera con la URL entra.

### 2. Secrets
Editá `roles/install-remote-workspace/defaults/secrets.yml` (gitignored) con:
- `remote_workspace_code_password` — password de login de code-server
- `remote_workspace_webdav_user` / `_password` — credenciales WebDAV (Finder)
- `remote_workspace_cloudflared_token` — el token del paso 1

### 3. Deploy
```bash
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags remote-workspace
```
Idempotente. Espera el rollout de `remote-workspace` y `cloudflared`.

### 4. Instalar Claude Code dentro de code-server (una vez)
Desde la laptop: abrí `https://claude.tudominio.com` → password → terminal:
```bash
curl -fsSL https://claude.ai/install.sh | bash    # instala en ~/.local/bin (= /config, persiste en el PVC)
claude --version
claude            # login: pega el token desde claude.ai (se abre URL, copiás el code)
```
`HOME=/config` está en el PVC, así que `claude` y su auth **sobreviven reinicios**.

Cloná tus proyectos en `/config/workspace` (el workspace por defecto).

### 5. Montar el disco en la Mac (Finder)
Finder → **Ir → Conectarse al servidor** (⌘K) → `https://files.tudominio.com`
→ usuario/clave WebDAV. Aparece como disco montado; drag&drop nativo. Es la
misma carpeta `/config/workspace` que ves en code-server.

## Por qué Cloudflare Tunnel y no Tailscale
Zscaler suele bloquear WireGuard/UDP (control plane de Tailscale incluido).
cloudflared es HTTPS saliente a la red de Cloudflare → pasa casi siempre. El rol
`install-tailscale` queda como fallback (ver skill `remote-access`).

## Gotchas
- **Multi-attach**: PVC es RWO y lo comparten 2 containers — por eso van en el
  MISMO pod con `strategy: Recreate`. No escalar `remote-workspace` a >1 réplica.
- **claude no persiste**: si lo instalaste con `npm -g` puede ir a `/usr/local`
  (NO persiste). Usá el `install.sh` nativo → `~/.local/bin` = `/config` = PVC.
- **WebDAV en Finder lento/no monta**: asegurate de `https://` (no `http://`) y
  que la app de Cloudflare Access permita el user-agent de WebDAV (Access puede
  pedir login que Finder no resuelve — para `files.` conviene policy por
  **service token** o un bypass por IP, no email OTP).
- **Zscaler rompe TLS inspection**: si el cert de Cloudflare lo reemplaza Zscaler,
  el browser igual confía (es CA corporativa). WebDAV en Finder puede quejarse del
  cert MITM — ahí quizá necesites el `files.` por un path que Zscaler no inspeccione.
- **Token-managed tunnel**: el routing hostname→service vive en el dashboard de
  Cloudflare, NO en el cluster. Cambios de hostname se hacen ahí.

## Verificar
```bash
kubectl -n workspace get pods,svc,pvc
kubectl -n workspace logs deploy/cloudflared | grep -i "registered tunnel"   # túnel arriba
kubectl -n workspace exec deploy/remote-workspace -c code-server -- ls /config/workspace
```
Desde la laptop corporativa: abrir `https://claude.tudominio.com` en el browser.
