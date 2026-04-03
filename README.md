# Run

Initiate the install:

- `ansible-playbook ./playbooks/bootstrap.yml -i ./inventory/hosts.ini`

# Removal

To remove K3s and contents run `ansible-playbook ./playbooks/uninstall.yml -i ./inventory/hosts.ini`
