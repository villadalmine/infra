# install-cifs-nas: Shared CIFS/SMB1 Storage for K3s (NAS LG N2R1)

Este rol instala el CSI SMB driver via Helm y monta un recurso compartido de un NAS LG N2R1 V1 (sólo SMB1) como almacenamiento persistente en Kubernetes, con validación automática de lectura/escritura.

- Driver: `smb.csi.k8s.io` (instalado via Helm chart `csi-driver-smb`)
- Protocolo: SMB1/CIFS (vers=1.0)
- Autenticación: credenciales inyectadas desde Secret via `nodeStageSecretRef`
- Tag Ansible: `storage`

## Variables

### `defaults/main.yml` — configuración general
```yaml
cifs_nas_ip: "192.168.178.102"     # IP del NAS
cifs_nas_share: "service"           # share raíz en el NAS
cifs_pv_size: "5Gi"
cifs_pv_name: "smb-nas-pv"
cifs_pvc_name: "smb-nas-pvc"
cifs_namespace: "default"
cifs_uid: 1000                      # UID mapeado en el mount
cifs_gid: 1000                      # GID mapeado en el mount
```

### `defaults/secrets.yml` — credenciales (gitignored)
```yaml
cifs_nas_user: "admin"
cifs_nas_pass: "changeme"
```

Copia `defaults/secrets.yml.example` → `defaults/secrets.yml` y completa con tus credenciales.

## ¿Qué aplica este rol?

1. **Helm**: instala/actualiza `csi-driver-smb` en `kube-system`
2. **Secret**: credenciales CIFS (lee de `secrets.yml`)
3. **PersistentVolume**: con `mountOptions` SMB1 (vers=1.0, uid/gid, file/dir_mode=0777, noperm)
4. **PersistentVolumeClaim**: bind al PV con `storageClassName: ""`
5. **Pod de test**: busybox que monta el volumen con `subPath: Torrent` y valida escritura

## MountOptions

El PV usa exactamente las opciones que funcionan en el kernel del nodo:

```yaml
mountOptions:
  - vers=1.0
  - uid=1000
  - gid=1000
  - file_mode=0777
  - dir_mode=0777
  - noperm
```

**Notas críticas:**
- `mountOptions` va a nivel `spec:` del PV, NO dentro de `csi:`
- `sec=ntlm` fue removido — kernels modernos lo rechazan ("bad security option: ntlm")
- `noserverino` y `cache=none` causan conflicto con la inyección de credenciales del CSI
- El CSI driver inyecta username/password automáticamente desde `nodeStageSecretRef`

## Uso

```bash
# Copiar y completar secrets
cp roles/install-cifs-nas/defaults/secrets.yml.example \
   roles/install-cifs-nas/defaults/secrets.yml
# Editar roles/install-cifs-nas/defaults/secrets.yml

# Ejecutar
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini --tags storage
```

## Subcarpetas del share

El share raíz (`//IP/service`) contiene subdirectorios como `Torrent`, `DLNA`, etc.
El pod de test monta `subPath: Torrent` para acceder directamente a esa carpeta en `/mnt/smb`.

Si necesitas otra subcarpeta, cambia `subPath` en el template `pod-cifs-test.yaml.j2`.

## Troubleshooting

### "username specified with no parameter"
- Falta `nodeStageSecretRef` en el PV
- El Secret no existe o tiene keys incorrectas (`username`/`password`)

### "bad security option: ntlm"
- Kernel moderno rechaza `sec=ntlm`. Remover de mountOptions.
- El CSI driver inyecta credenciales automáticamente, no hace falta `sec=`.

### "Permission denied" en el pod
- Verificar que `cifs_uid`/`cifs_gid` coincidan con `securityContext.runAsUser`/`runAsGroup` del pod
- Verificar permisos RW en el NAS para el usuario configurado

### PVC no bindea al PV
- Ambos deben tener `storageClassName: ""` (string vacío)
- El PVC debe referenciar el PV correcto via `volumeName`

### Debug manual en el nodo
```bash
sudo mount -t cifs //192.168.178.102/service /mnt/nas-test \
  -o username=admin,password=elendil123,vers=1.0,uid=1000,gid=1000,file_mode=0777,dir_mode=0777,noperm
```

## Referencias

- https://github.com/kubernetes-csi/csi-driver-smb
- https://github.com/kubernetes-csi/csi-driver-smb/blob/master/docs/volume-mount-options.md
