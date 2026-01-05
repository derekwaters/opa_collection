derekwaters.opa_collection.service
==================================

This role allows you to install or remove a systemd service to run an OpenPolicyAgent (OPA) server.

Requirements
------------

None.

Role Variables
--------------

The following variables are used by this role:

Input Variables:
---
# vars file for opa_service

opa_service:
  state:              Whether the service should be present or absent (required, [absent / present])
                      
Output Variables:

Dependencies
------------

None.

Example Playbook
----------------

The following playbook installs the OPA service on the nominated host.

    - name: Ensure the OPA Service is present
      ansible.builtin.include_role:
        name: derekwaters.opa.service
      vars:
        opa_service:
          state: present
            
License
-------

GPL3

Author Information
------------------

Derek Waters
email: derek@frisbeeworld.com
https://github.com/derekwaters
