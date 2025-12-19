#!/usr/bin/python
# coding: utf-8 -*-

from __future__ import absolute_import, division, print_function

__metaclass__ = type


ANSIBLE_METADATA = {'metadata_version': '1.1', 'status': ['preview'], 'supported_by': 'community'}


DOCUMENTATION = '''
---
module: user
author: "John Westcott IV (@john-westcott-iv)"
short_description: create, update, or destroy Automation Platform Controller users.
description:
    - Create, update, or destroy Automation Platform Controller users. See
      U(https://www.ansible.com/tower) for an overview.
options:
    username:
      description:
        - Required. 150 characters or fewer. Letters, digits and @/./+/-/_ only.
      required: True
      type: str
    new_username:
      description:
        - Setting this option will change the existing username (looked up via the name field.
      type: str
    first_name:
      description:
        - First name of the user.
      type: str
    last_name:
      description:
        - Last name of the user.
      type: str
    email:
      description:
        - Email address of the user.
      type: str
    organization:
      description:
        - The user will be created as a member of that organization name, ID, or named URL (needed for organization admins to create new organization users).
      type: str
    is_superuser:
      description:
        - Designates that this user has all permissions without explicitly assigning them.
      type: bool
      aliases: ['superuser']
    is_system_auditor:
      description:
        - User is a system wide auditor.
      type: bool
      aliases: ['auditor']
    password:
      description:
        - Write-only field used to change the password.
      type: str
    update_secrets:
      description:
        - C(true) will always change password if user specifies password, even if API gives $encrypted$ for password.
        - C(false) will only set the password if other values change too.
      type: bool
      default: true
    state:
      description:
        - Desired state of the resource.
      choices: ["present", "absent", "exists"]
      default: "present"
      type: str
extends_documentation_fragment: awx.awx.auth
'''


EXAMPLES = '''
- name: Add user
  user:
    username: jdoe
    password: foobarbaz
    email: jdoe@example.org
    first_name: John
    last_name: Doe
    state: present
    controller_config_file: "~/tower_cli.cfg"

- name: Add user as a system administrator
  user:
    username: jdoe
    password: foobarbaz
    email: jdoe@example.org
    superuser: true
    state: present
    controller_config_file: "~/tower_cli.cfg"

- name: Add user as a system auditor
  user:
    username: jdoe
    password: foobarbaz
    email: jdoe@example.org
    auditor: true
    state: present
    controller_config_file: "~/tower_cli.cfg"

- name: Add user as a member of an organization (permissions on the organization are required)
  user:
    username: jdoe
    password: foobarbaz
    email: jdoe@example.org
    organization: devopsorg
    state: present

- name: Delete user
  user:
    username: jdoe
    email: jdoe@example.org
    state: absent
    controller_config_file: "~/tower_cli.cfg"
'''

from ..module_utils.opa_api import OPAAPIModule


def main():
    # Any additional arguments that are not fields of the item can be added here
    argument_spec = dict(
        id=dict(required=True),
        definition=dict(type='str'),
        state=dict(choices=['present', 'absent', 'exists'], default='present'),
    )

    # Create a module for ourselves
    module = OPAAPIModule(argument_spec=argument_spec)

    # Extract our parameters
    id = module.params.get('id')
    definition = module.params.get('definition')
    state = module.params.get('state')

    # Attempt to look up an existing item based on the provided data
    existing_item = module.get_one('policies', id=id, check_exists=(state == 'exists'))

    if state == 'absent':
        # If the state was absent we can let the module delete it if needed, the module will handle exiting from this
        module.delete_if_needed(existing_item)

    # Create the data that gets sent for create and update
    new_fields = {}
    if definition is not None:
        new_fields['definition'] = definition

    # If the state was present and we can let the module build or update the existing item, this will return on its own
    module.create_or_update_if_needed(existing_item, new_fields, endpoint='policies', item_type='policy')


if __name__ == '__main__':
    main()