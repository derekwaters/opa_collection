derekwaters.opa_collection.query
================================

This role allows you to perform queries against policies in a running OpenPolicyAgent (OPA) server.

Requirements
------------

None.

Role Variables
--------------

The following variables are used by this role:

Input Variables:

opa_host:             The hostname of the OpenPolicyAgent server. (required)
opa_port:             The port of the OpenPolicyAgent server. (optional, default '8181')
opa_policy_namespace: The namespace of the policy to evaluate. (required)
opa_policy_rule:      The name of the policy rule to evaluate. (required)
opa_input_data:       An object containing all of the data that will be provided to
                      OPA to evaluate the policy against. (required)
                      
Output Variables:

opa_query_result  The results of the query operation are stored in this value.
                  The json member value will contain the json data returned from OPA.

Dependencies
------------

None.

Example Playbook
----------------

The following playbook tests input data against a pre-loaded rego policy 
on an OPA server hosted on opa.example.com in the test_policy namespace.

    - name: Test against a policy
      ansible.builtin.include_role:
        name: derekwaters.opa.query
      vars:
        opa_host: opa.example.com
        opa_port: 8181
        opa_policy_namespace: test_policy
        opa_policy_rule: sample
        opa_input_data:
          extra_vars:
            aaa: bbb
            
License
-------

GPL3

Author Information
------------------

Derek Waters
email: derek@frisbeeworld.com
https://github.com/derekwaters
