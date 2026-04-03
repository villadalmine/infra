# Run

Initiate the install:

- `ansible-playbook ./playbooks/bootstrap.yml -i ./inventory/hosts.ini`

# Removal

To remove K3s and contents of `/mnt/data` run `ansible-playbook ./playbooks/uninstall.yml -i ./inventory/hosts.ini`
