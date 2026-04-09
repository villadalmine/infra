Ansible Vault: storing LiteLLM / OpenRouter keys

Quick steps

1. Copy the example vault file:

   cp group_vars/all/vault.yml.example group_vars/all/vault.yml

2. Encrypt it with Ansible Vault (choose a strong passphrase):

   ansible-vault encrypt group_vars/all/vault.yml

3. Edit `group_vars/all/vault.yml` and replace the placeholder keys with real OpenRouter API keys.

4. Run the playbook as usual. The roles will read the keys from the vault variables and populate Kubernetes Secrets.

Notes
- Do NOT commit `group_vars/all/vault.yml` to git. Keep the encrypted file or store the passphrase securely.
- If you need immediate mitigation, rotate the keys that were committed and then follow this pattern.
