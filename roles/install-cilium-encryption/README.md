# Cilium WireGuard Encryption Role

This role enables WireGuard transparent encryption on an existing Cilium installation.

## ⚠️ CRITICAL COMPATIBILITY REQUIREMENTS

**nodeEncryption compatibility depends on your networking stack:**

| Network Stack | nodeEncryption | Compatible? | Notes |
|---|---|---|---|
| **Cilium + LB-IPAM + L2 Announcements** | `false` | ✅ **YES** | **Our current setup** |
| **Cilium + LB-IPAM + L2 Announcements** | `true` | ❌ **NO** | **BREAKS LoadBalancer traffic** |
| Cilium + BGP + MetalLB | `true` | ✅ YES | Full node encryption works |
| Cilium + BGP + Native routing | `true` | ✅ YES | Full node encryption works |
| Cilium + CNI Chaining (AWS VPC, Azure) | `false` | ⚠️ RECOMMENDED | Requires MTU adjustment |

## Configuration Examples

### Example 1: LB-IPAM Compatible (DEFAULT - Our Setup)

```yaml
# inventory/group_vars/all.yml or playbook vars
cilium_encryption_enabled: true
cilium_encryption_type: "wireguard"
cilium_encryption_node_encryption_override: false  # CRITICAL: Must be false
```

**Result:**
- ✅ Pod-to-pod traffic: ENCRYPTED
- ✅ LoadBalancer traffic (external → service): UNENCRYPTED (works correctly)
- ✅ LB-IPAM + L2 announcements: FUNCTIONAL

### Example 2: Full Node Encryption (BGP Stacks Only)

```yaml
# inventory/group_vars/all.yml - FOR BGP STACKS ONLY
cilium_encryption_enabled: true
cilium_encryption_type: "wireguard"
cilium_encryption_node_encryption_override: true    # Can be enabled with BGP
cilium_encryption_strict_mode_enabled: true         # Enforce encryption
cilium_encryption_strict_mode_egress_cidr: "10.0.0.0/8"
```

**Result:**
- ✅ Pod-to-pod traffic: ENCRYPTED
- ✅ Node-to-node traffic: ENCRYPTED
- ⚠️ Requires BGP routing (NOT compatible with LB-IPAM)

### Example 3: CNI Chaining Compatible

```yaml  
# inventory/group_vars/all.yml - For AWS VPC CNI, Azure CNI, etc.
cilium_encryption_enabled: true
cilium_encryption_type: "wireguard"
cilium_encryption_node_encryption_override: false           # Recommended for chaining
cilium_encryption_cni_route_mtu_chaining: true              # REQUIRED for chaining
```

**Result:**
- ✅ Pod-to-pod traffic: ENCRYPTED
- ✅ Compatible with AWS VPC CNI, Azure CNI
- ✅ Proper MTU handling for tunneled traffic

## Usage

### Basic Deployment (Our LB-IPAM Stack)

```bash
# Deploy pod-to-pod encryption (compatible with LB-IPAM)
make encryption
```

### Advanced Deployment with Custom Parameters

```bash
# Deploy with specific configuration
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --tags encryption \
  -e cilium_encryption_node_encryption_override=false \
  -e cilium_encryption_strict_mode_enabled=false
```

### Dry Run (Preview Changes)

```bash
# See what changes will be applied without making them
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --tags encryption \
  -e cilium_encryption_dry_run=true
```

## Validation

After deployment, verify encryption is working:

```bash
# 1. Check encryption status
kubectl exec -n kube-system ds/cilium -- cilium-dbg status | grep -i encrypt

# 2. Verify WireGuard interfaces (if nodeEncryption: true)  
ssh to-any-node
ip link show type wireguard

# 3. Check encryption metrics
kubectl port-forward -n kube-system svc/hubble-metrics 9965:9965 &
curl -s localhost:9965/metrics | grep -E "hubble.*encrypted"

# 4. Test LoadBalancer connectivity (critical!)
curl -k https://192.168.178.200  # Should work if LB-IPAM compatible
```

## Emergency Rollback

If encryption deployment breaks the cluster:

```bash
# 1. Immediate rollback
helm upgrade cilium cilium/cilium --version 1.19.2 \
  --namespace kube-system --reuse-values \
  --set encryption.enabled=false \
  --timeout=5m

# 2. Force pod restart if needed
kubectl rollout restart daemonset/cilium -n kube-system
kubectl rollout restart deployment/cilium-operator -n kube-system

# 3. Verify connectivity restored
curl -k --connect-timeout 5 https://192.168.178.200
```

## Troubleshooting

### Problem: LoadBalancer services become unreachable after encryption

**Cause:** `nodeEncryption: true` is incompatible with LB-IPAM + L2 announcements

**Solution:** 
```bash
# Redeploy with nodeEncryption: false
ansible-playbook playbooks/bootstrap.yml -i inventory/hosts.ini \
  --tags encryption \
  -e cilium_encryption_node_encryption_override=false
```

### Problem: Cilium operator pods restart repeatedly

**Cause:** WireGuard configuration conflicts or API server timeouts

**Solution:**
```bash
# Check operator logs
kubectl logs -n kube-system -l io.cilium/app=operator --tail=50

# Rollback and redeploy with correct settings
make encryption  # Uses safe defaults
```

### Problem: Pods stuck in Init:0/6 state

**Cause:** WireGuard configuration preventing pod initialization

**Solution:**
```bash
# Force rollout restart
kubectl rollout restart daemonset/cilium -n kube-system

# If that fails, disable encryption temporarily
helm upgrade cilium cilium/cilium --version 1.19.2 \
  --namespace kube-system --reuse-values \
  --set encryption.enabled=false
```

## See Also

- `ENCRYPTION_PLAN.md` - Detailed implementation plan and lessons learned
- `skills/cilium/` - Cilium-specific troubleshooting guides
- [Cilium WireGuard Documentation](https://docs.cilium.io/en/v1.19/security/network/encryption-wireguard/) - Official docs