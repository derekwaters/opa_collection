Role Name
=========

This role allows you to create, update and delete policy definitions in a running OpenPolicyAgent (OPA) server.

Requirements
------------

None.

Role Variables
--------------

The following variables are used by this role:

Input Variables:

opa_host:     The hostname of the OpenPolicyAgent server. (required)
opa_port:     The port of the OpenPolicyAgent server. (optional, default '8181')
opa_policy:   The policy object being created, updated or deleted.
  namespace   The policy namespace / identifier. (required)
  state       The state of the policy. (required, 'present' or 'absent')
  data        The plaintext rego data for the policy. (required if state is 'present')

Output Variables:

opa_policy_result   The results of the policy operation are stored in this value.

Dependencies
------------

None.

Example Playbook
----------------

The following playbook adds a new rego policy to an OPA server hosted on 
opa.example.com in the test_policy namespace.

    - name: Add a new policy
      ansible.builtin.include_role:
        name: derekwaters.opa.policy
      vars:
        opa_host: opa.example.com
        opa_port: 8181
        opa_policy:
          namespace: test_policy
          state: present
          data: |
            ...rego definition...
  
License
-------

GPL3

Author Information
------------------

Derek Waters
email: derek@frisbeeworld.com
https://github.com/derekwaters
