from __future__ import absolute_import, division, print_function

__metaclass__ = type

from ansible.module_utils.basic import AnsibleModule, env_fallback
from ansible.module_utils.urls import Request, SSLValidationError, ConnectionError
from ansible.module_utils.parsing.convert_bool import boolean as strtobool
from ansible.module_utils.six import PY2
from ansible.module_utils.six import raise_from
from ansible.module_utils.six.moves import StringIO
from ansible.module_utils.six.moves.urllib.error import HTTPError
from ansible.module_utils.six.moves.http_cookiejar import CookieJar
from ansible.module_utils.six.moves.urllib.parse import urlparse, urlencode, quote
from ansible.module_utils.six.moves.configparser import ConfigParser, NoOptionError
from base64 import b64encode
from socket import getaddrinfo, IPPROTO_TCP
import time
from json import loads, dumps
from os.path import isfile, expanduser, split, join, exists, isdir
from os import access, R_OK, getcwd, environ, getenv


try:
    from ansible.module_utils.compat.version import LooseVersion as Version
except ImportError:
    try:
        from distutils.version import LooseVersion as Version
    except ImportError:
        raise AssertionError('To use this plugin or module with ansible-core 2.11, you need to use Python < 3.12 with distutils.version present')

try:
    import yaml

    HAS_YAML = True
except ImportError:
    HAS_YAML = False


class ConfigFileException(Exception):
    pass


class ItemNotDefined(Exception):
    pass


class OPAModule(AnsibleModule):
    url = None
    AUTH_ARGSPEC = dict(
        opa_host=dict(
            required=False,
            fallback=(env_fallback, ['OPA_HOST'])),
        validate_certs=dict(
            type='bool',
            aliases=['verify_ssl', 'opa_validate_certs'],
            required=False,
            fallback=(env_fallback, ['VERIFY_SSL', 'OPA_VALIDATE_CERTS', 'VALIDATE_CERTS'])),
    )
    short_params = {
        'host': 'opa_host',
        'verify_ssl': 'validate_certs',
    }
    host = 'https://127.0.0.1:8181'
    verify_ssl = True
    error_callback = None
    warn_callback = None

    def __init__(self, argument_spec=None, direct_params=None, error_callback=None, warn_callback=None, **kwargs):
        full_argspec = {}
        full_argspec.update(ControllerModule.AUTH_ARGSPEC)
        full_argspec.update(argument_spec)
        kwargs['supports_check_mode'] = True

        self.error_callback = error_callback
        self.warn_callback = warn_callback

        self.json_output = {'changed': False}

        if direct_params is not None:
            self.params = direct_params
        else:
            super().__init__(argument_spec=full_argspec, **kwargs)

        self.load_config_files()

        # Parameters specified on command line will override settings in any config
        for short_param, long_param in self.short_params.items():
            direct_value = self.params.get(long_param)
            if direct_value is not None:
                setattr(self, short_param, direct_value)

        # Perform some basic validation
        if not self.host.startswith(("https://", "http://")):  # NOSONAR
            self.host = "https://{0}".format(self.host)

        # Try to parse the hostname as a url
        try:
            self.url = urlparse(self.host)
            # Store URL prefix for later use in build_url
            self.url_prefix = self.url.path
        except Exception as e:
            self.fail_json(msg="Unable to parse controller_host as a URL ({1}): {0}".format(self.host, e))

        # Remove ipv6 square brackets
        remove_target = '[]'
        for char in remove_target:
            self.url.hostname.replace(char, "")
        # Try to resolve the hostname
        try:
            proxy_env_var_name = "{0}_proxy".format(self.url.scheme)
            if not environ.get(proxy_env_var_name) and not environ.get(proxy_env_var_name.upper()):
                addrinfolist = getaddrinfo(self.url.hostname, self.url.port, proto=IPPROTO_TCP)
                for family, kind, proto, canonical, sockaddr in addrinfolist:
                    sockaddr[0]
        except Exception as e:
            self.fail_json(msg="Unable to resolve controller_host ({1}): {0}".format(self.url.hostname, e))

    def build_url(self, endpoint, query_params=None, app_key=None):
        # Make sure we start with /api/vX
        if not endpoint.startswith("/"):
            endpoint = "/{0}".format(endpoint)
        hostname_prefix = self.url_prefix.rstrip("/")
        api_path = self.api_path(app_key=app_key)
        api_version = '/v1/'
        if not endpoint.startswith(hostname_prefix + api_path):
            endpoint = hostname_prefix + f"{api_path}{api_version}{endpoint}"
        if not endpoint.endswith('/') and '?' not in endpoint:
            endpoint = "{0}/".format(endpoint)

        # Update the URL path with the endpoint
        url = self.url._replace(path=endpoint)

        if query_params:
            url = url._replace(query=urlencode(query_params))

        return url

    def fail_json(self, **kwargs):
        if self.error_callback:
            self.error_callback(**kwargs)
        else:
            super().fail_json(**kwargs)

    def exit_json(self, **kwargs):
        super().exit_json(**kwargs)

    def warn(self, warning):
        if self.warn_callback is not None:
            self.warn_callback(warning)
        else:
            super().warn(warning)


class OPAAPIModule(OPAModule):
    # TODO: Move the collection version check into controller_module.py
    # This gets set by the make process so whatever is in here is irrelevant
    _COLLECTION_VERSION = "0.0.1-devel"
    _COLLECTION_TYPE = "awx"
    # This maps the collections type (awx/tower) to the values returned by the API
    # Those values can be found in awx/api/generics.py line 204
    collection_to_version = {
        'awx': 'AWX',
        'controller': 'Red Hat Ansible Automation Platform',
    }
    session = None
    IDENTITY_FIELDS = {'policies': 'id'}
    ENCRYPTED_STRING = "$encrypted$"

    def __init__(self, argument_spec, direct_params=None, error_callback=None, warn_callback=None, **kwargs):
        kwargs['supports_check_mode'] = True

        super().__init__(argument_spec=argument_spec, direct_params=direct_params, error_callback=error_callback, warn_callback=warn_callback, **kwargs)
        self.session = Request(cookies=CookieJar(), timeout=self.request_timeout, validate_certs=self.verify_ssl)

    @staticmethod
    def get_name_field_from_endpoint(endpoint):
        return OPAAPIModule.IDENTITY_FIELDS.get(endpoint, 'name')

    def get_item_name(self, item, allow_unknown=False):
        if item:
            if 'name' in item:
                return item['name']

            for field_name in OPAAPIModule.IDENTITY_FIELDS.values():
                if field_name in item:
                    return item[field_name]

        if allow_unknown:
            return 'unknown'

        if item:
            self.exit_json(msg='Cannot determine identity field for {0} object.'.format(item.get('type', 'unknown')))
        else:
            self.exit_json(msg='Cannot determine identity field for Undefined object.')

    def head_endpoint(self, endpoint, *args, **kwargs):
        return self.make_request('HEAD', endpoint, **kwargs)

    def get_endpoint(self, endpoint, *args, **kwargs):
        return self.make_request('GET', endpoint, **kwargs)

    def patch_endpoint(self, endpoint, *args, **kwargs):
        # Handle check mode
        if self.check_mode:
            self.json_output['changed'] = True
            self.exit_json(**self.json_output)

        return self.make_request('PATCH', endpoint, **kwargs)

    def post_endpoint(self, endpoint, *args, **kwargs):
        # Handle check mode
        if self.check_mode:
            self.json_output['changed'] = True
            self.exit_json(**self.json_output)

        return self.make_request('POST', endpoint, **kwargs)

    def delete_endpoint(self, endpoint, *args, **kwargs):
        # Handle check mode
        if self.check_mode:
            self.json_output['changed'] = True
            self.exit_json(**self.json_output)

        return self.make_request('DELETE', endpoint, **kwargs)

    def get_all_endpoint(self, endpoint, *args, **kwargs):
        response = self.get_endpoint(endpoint, *args, **kwargs)
        if 'next' not in response['json']:
            raise RuntimeError('Expected list from API at {0}, got: {1}'.format(endpoint, response))
        next_page = response['json']['next']

        if response['json']['count'] > 10000:
            self.fail_json(msg='The number of items being queried for is higher than 10,000.')

        while next_page is not None:
            next_response = self.get_endpoint(next_page)
            response['json']['results'] = response['json']['results'] + next_response['json']['results']
            next_page = next_response['json']['next']
            response['json']['next'] = next_page
        return response

    def get_one(self, endpoint, id=None, allow_none=True, check_exists=False, **kwargs):
        new_kwargs = kwargs.copy()
        response = None

        # Since we didn't have a named URL, lets try and find it with a general search
        if response is None:
            if id:
                new_data = kwargs.get('data', {}).copy()
                new_data['id'] = id
                new_kwargs['data'] = new_data

            response = self.get_endpoint(endpoint, **new_kwargs)

            if response['status_code'] != 200:
                fail_msg = "Got a {0} response when trying to get one from {1}".format(response['status_code'], endpoint)
                if 'detail' in response.get('json', {}):
                    fail_msg += ', detail: {0}'.format(response['json']['detail'])
                self.fail_json(msg=fail_msg)

            if 'count' not in response['json'] or 'results' not in response['json']:
                self.fail_json(msg="The endpoint did not provide count and results")

        if response['json']['count'] == 0:
            if allow_none:
                return None
            else:
                self.fail_wanted_one(response, endpoint, new_kwargs.get('data'))
        elif response['json']['count'] > 1:
            if id:
                # Since we did a name or ID search and got > 1 return something if the id matches
                for asset in response['json']['results']:
                    if str(asset['id']) == id:
                        return asset
            # We got > 1 and either didn't find something by ID (which means multiple names)
            # Or we weren't running with a or search and just got back too many to begin with.
            self.fail_wanted_one(response, endpoint, new_kwargs.get('data'))

        if check_exists:
            self.json_output['id'] = response['json']['results'][0]['id']
            self.exit_json(**self.json_output)

        return response['json']['results'][0]

    def fail_wanted_one(self, response, endpoint, query_params):
        sample = response.copy()
        if len(sample['json']['results']) > 1:
            sample['json']['results'] = sample['json']['results'][:2] + ['...more results snipped...']
        url = self.build_url(endpoint, query_params)
        host_length = len(self.host)
        display_endpoint = url.geturl()[host_length:]  # truncate to not include the base URL
        self.fail_json(
            msg="Request to {0} returned {1} items, expected 1".format(display_endpoint, response['json']['count']),
            query=query_params,
            response=sample,
            total_results=response['json']['count'],
        )

    def get_exactly_one(self, endpoint, name_or_id=None, **kwargs):
        return self.get_one(endpoint, name_or_id=name_or_id, allow_none=False, **kwargs)

    def make_request(self, method, endpoint, *args, **kwargs):
        # In case someone is calling us directly; make sure we were given a method, let's not just assume a GET
        if not method:
            raise Exception("The HTTP method must be defined")

        if method in ['POST', 'PUT', 'PATCH']:
            url = self.build_url(endpoint)
        else:
            url = self.build_url(endpoint, query_params=kwargs.get('data'))

        # Extract the headers, this will be used in a couple of places
        headers = kwargs.get('headers', {})

        if method in ['POST', 'PUT', 'PATCH']:
            headers.setdefault('Content-Type', 'application/json')
            kwargs['headers'] = headers

        data = None  # Important, if content type is not JSON, this should not be dict type
        if headers.get('Content-Type', '') == 'application/json':
            data = dumps(kwargs.get('data', {}))

        try:
            response = self.session.open(
                method, url.geturl(),
                headers=headers,
                timeout=self.request_timeout,
                validate_certs=self.verify_ssl,
                follow_redirects=True,
                data=data
            )
        except (SSLValidationError) as ssl_err:
            self.fail_json(msg="Could not establish a secure connection to your host ({1}): {0}.".format(url.netloc, ssl_err))
        except (ConnectionError) as con_err:
            self.fail_json(msg="There was a network error of some kind trying to connect to your host ({1}): {0}.".format(url.netloc, con_err))
        except (HTTPError) as he:
            # Sanity check: Did the server send back some kind of internal error?
            if he.code >= 500:
                self.fail_json(msg='The host sent back a server error ({1}): {0}. Please check the logs and try again later'.format(url.path, he))
            # Sanity check: Did we fail to authenticate properly?  If so, fail out now; this is always a failure.
            elif he.code == 401 or he.code == 403:
                self.fail_json(msg='Invalid authentication credentials for {0} (HTTP 401).'.format(url.path))
            # Sanity check: Did we get a 404 response?
            # Requests with primary keys will return a 404 if there is no response, and we want to consistently trap these.
            elif he.code == 404:
                if kwargs.get('return_none_on_404', False):
                    return None
                self.fail_json(msg='The requested object could not be found at {0}.'.format(url.path))
            # Sanity check: Did we get a 405 response?
            # A 405 means we used a method that isn't allowed. Usually this is a bad request, but it requires special treatment because the
            # API sends it as a logic error in a few situations (e.g. trying to cancel a job that isn't running).
            elif he.code == 405:
                self.fail_json(msg="Cannot make a request with the {0} method to this endpoint {1}".format(method, url.path))
            # Sanity check: Did we get some other kind of error?  If so, write an appropriate error message.
            elif he.code >= 400:
                # We are going to return a 400 so the module can decide what to do with it
                page_data = he.read()
                try:
                    return {'status_code': he.code, 'json': loads(page_data)}
                # JSONDecodeError only available on Python 3.5+
                except ValueError:
                    return {'status_code': he.code, 'text': page_data}
            elif he.code == 204 and method == 'DELETE':
                # A 204 is a normal response for a delete function
                pass
            else:
                self.fail_json(msg="Unexpected return code when calling {0}: {1}".format(url.geturl(), he))
        except (Exception) as e:
            self.fail_json(msg="There was an unknown error when trying to connect to {2}: {0} {1}".format(type(e).__name__, e, url.geturl()))

        response_body = ''
        try:
            response_body = response.read()
        except (Exception) as e:
            self.fail_json(msg="Failed to read response body: {0}".format(e))

        response_json = {}
        if response_body and response_body != '':
            try:
                response_json = loads(response_body)
            except (Exception) as e:
                self.fail_json(msg="Failed to parse the response json: {0}".format(e))

        status_code = response.status
        return {'status_code': status_code, 'json': response_json}

    def api_path(self, app_key=None):
        return '/v1/'

    def delete_if_needed(self, existing_item, item_type=None, on_delete=None, auto_exit=True):
        # This will exit from the module on its own.
        # If the method successfully deletes an item and on_delete param is defined,
        #   the on_delete parameter will be called as a method pasing in this object and the json from the response
        # This will return one of two things:
        #   1. None if the existing_item is not defined (so no delete needs to happen)
        #   2. The response from AWX from calling the delete on the endpont. It's up to you to process the response and exit from the module
        # Note: common error codes from the AWX API can cause the module to fail
        if existing_item:
            # If we have an item, we can try to delete it
            try:
                item_url = existing_item['url']
                item_id = existing_item['id']
                if not item_type:
                    item_type = existing_item['type']
                item_name = self.get_item_name(existing_item, allow_unknown=True)
            except KeyError as ke:
                self.fail_json(msg="Unable to process delete of item due to missing data {0}".format(ke))

            response = self.delete_endpoint(item_url)

            if response['status_code'] in [202, 204]:
                if on_delete:
                    on_delete(self, response['json'])
                self.json_output['changed'] = True
                self.json_output['id'] = item_id
                self.exit_json(**self.json_output)
                if auto_exit:
                    self.exit_json(**self.json_output)
                else:
                    return self.json_output
            else:
                if 'json' in response and '__all__' in response['json']:
                    self.fail_json(msg="Unable to delete {0} {1}: {2}".format(item_type, item_name, response['json']['__all__'][0]))
                elif 'json' in response:
                    # This is from a project delete (if there is an active job against it)
                    if 'error' in response['json']:
                        self.fail_json(msg="Unable to delete {0} {1}: {2}".format(item_type, item_name, response['json']['error']))
                    else:
                        self.fail_json(msg="Unable to delete {0} {1}: {2}".format(item_type, item_name, response['json']))
                else:
                    self.fail_json(msg="Unable to delete {0} {1}: {2}".format(item_type, item_name, response['status_code']))
        else:
            if auto_exit:
                self.exit_json(**self.json_output)
            else:
                return self.json_output

    def copy_item(self, existing_item, copy_from_name_or_id, new_item_name, endpoint=None, item_type='unknown', copy_lookup_data=None):

        if existing_item is not None:
            self.warn("A {0} with the name {1} already exists.".format(item_type, new_item_name))
            self.json_output['changed'] = False
            self.json_output['copied'] = False
            return existing_item

        # Lookup existing item to copy from
        copy_from_lookup = self.get_one(endpoint, name_or_id=copy_from_name_or_id, **{'data': copy_lookup_data})

        # Fail if the copy_from_lookup is empty
        if copy_from_lookup is None:
            self.fail_json(msg="A {0} with the name {1} was not able to be found.".format(item_type, copy_from_name_or_id))

        response = self.post_endpoint(copy_from_lookup['related']['copy'], **{'data': {'name': new_item_name}})

        if response['status_code'] in [201]:
            self.json_output['id'] = response['json']['id']
            self.json_output['changed'] = True
            self.json_output['copied'] = True
            new_existing_item = response['json']
        else:
            if 'json' in response and '__all__' in response['json']:
                self.fail_json(msg="Unable to create {0} {1}: {2}".format(item_type, new_item_name, response['json']['__all__'][0]))
            elif 'json' in response:
                self.fail_json(msg="Unable to create {0} {1}: {2}".format(item_type, new_item_name, response['json']))
            else:
                self.fail_json(msg="Unable to create {0} {1}: {2}".format(item_type, new_item_name, response['status_code']))
        return new_existing_item

    def create_if_needed(self, existing_item, new_item, endpoint, on_create=None, auto_exit=True, item_type='unknown'):

        # This will exit from the module on its own
        # If the method successfully creates an item and on_create param is defined,
        #    the on_create parameter will be called as a method pasing in this object and the json from the response
        # This will return one of two things:
        #    1. None if the existing_item is already defined (so no create needs to happen)
        #    2. The response from AWX from calling the patch on the endpont. It's up to you to process the response and exit from the module
        # Note: common error codes from the AWX API can cause the module to fail
        response = None
        if not endpoint:
            self.fail_json(msg="Unable to create new {0} due to missing endpoint".format(item_type))

        item_url = None
        if existing_item:
            try:
                item_url = existing_item['url']
            except KeyError as ke:
                self.fail_json(msg="Unable to process create of item due to missing data {0}".format(ke))
        else:
            # If we don't have an exisitng_item, we can try to create it

            # We have to rely on item_type being passed in since we don't have an existing item that declares its type
            # We will pull the item_name out from the new_item, if it exists
            item_name = self.get_item_name(new_item, allow_unknown=True)

            response = self.post_endpoint(endpoint, **{'data': new_item})

            # 200 is response from approval node creation on tower 3.7.3 or awx 15.0.0 or earlier.
            if response['status_code'] in [200, 201]:
                self.json_output['name'] = 'unknown'
                for key in ('name', 'username', 'identifier', 'hostname'):
                    if key in response['json']:
                        self.json_output['name'] = response['json'][key]
                self.json_output['id'] = response['json']['id']
                self.json_output['changed'] = True
                item_url = response['json']['url']
            else:
                if 'json' in response and '__all__' in response['json']:
                    self.fail_json(msg="Unable to create {0} {1}: {2}".format(item_type, item_name, response['json']['__all__'][0]))
                elif 'json' in response:
                    self.fail_json(msg="Unable to create {0} {1}: {2}".format(item_type, item_name, response['json']))
                else:
                    self.fail_json(msg="Unable to create {0} {1}: {2}".format(item_type, item_name, response['status_code']))

        # If we have an on_create method and we actually changed something we can call on_create
        if on_create is not None and self.json_output['changed']:
            on_create(self, response['json'])
        elif auto_exit:
            self.exit_json(**self.json_output)
        else:
            if response is not None:
                last_data = response['json']
                return last_data
            else:
                return

    @staticmethod
    def fields_could_be_same(old_field, new_field):
        """Treating $encrypted$ as a wild card,
        return False if the two values are KNOWN to be different
        return True if the two values are the same, or could potentially be the same,
        depending on the unknown $encrypted$ value or sub-values
        """
        if isinstance(old_field, dict) and isinstance(new_field, dict):
            if set(old_field.keys()) != set(new_field.keys()):
                return False
            for key in new_field.keys():
                if not OPAAPIModule.fields_could_be_same(old_field[key], new_field[key]):
                    return False
            return True  # all sub-fields are either equal or could be equal
        else:
            if old_field == OPAAPIModule.ENCRYPTED_STRING:
                return True
            return bool(new_field == old_field)

    def objects_could_be_different(self, old, new, field_set=None, warning=False):
        if field_set is None:
            field_set = set(fd for fd in new.keys() if fd not in ('modified', 'related', 'summary_fields'))
        for field in field_set:
            new_field = new.get(field, None)
            old_field = old.get(field, None)
            if old_field != new_field:
                if self.update_secrets or (not self.fields_could_be_same(old_field, new_field)):
                    return True  # Something doesn't match, or something might not match
            elif self.has_encrypted_values(new_field) or field not in new:
                if self.update_secrets or (not self.fields_could_be_same(old_field, new_field)):
                    # case of 'field not in new' - user password write-only field that API will not display
                    self._encrypted_changed_warning(field, old, warning=warning)
                    return True
        return False

    def update_if_needed(self, existing_item, new_item, item_type=None, on_update=None, auto_exit=True):
        # This will exit from the module on its own
        # If the method successfully updates an item and on_update param is defined,
        #   the on_update parameter will be called as a method pasing in this object and the json from the response
        # This will return one of two things:
        #    1. None if the existing_item does not need to be updated
        #    2. The response from AWX from patching to the endpoint. It's up to you to process the response and exit from the module.
        # Note: common error codes from the AWX API can cause the module to fail
        response = None
        if existing_item:

            # If we have an item, we can see if it needs an update
            try:
                item_url = existing_item['url']
                if not item_type:
                    item_type = existing_item['type']
                else:
                    item_name = existing_item['name']
                item_id = existing_item['id']
            except KeyError as ke:
                self.fail_json(msg="Unable to process update of item due to missing data {0}".format(ke))

            # Check to see if anything within the item requires the item to be updated
            needs_patch = self.objects_could_be_different(existing_item, new_item)

            # If we decided the item needs to be updated, update it
            self.json_output['id'] = item_id
            if needs_patch:
                response = self.patch_endpoint(item_url, **{'data': new_item})
                if response['status_code'] == 200:
                    # compare apples-to-apples, old API data to new API data
                    # but do so considering the fields given in parameters
                    self.json_output['changed'] |= self.objects_could_be_different(existing_item, response['json'], field_set=new_item.keys(), warning=True)
                elif 'json' in response and '__all__' in response['json']:
                    self.fail_json(msg=response['json']['__all__'])
                else:
                    self.fail_json(**{'msg': "Unable to update {0} {1}, see response".format(item_type, item_name), 'response': response})

        else:
            raise RuntimeError('update_if_needed called incorrectly without existing_item')

        # If we change something and have an on_change call it
        if on_update is not None and self.json_output['changed']:
            if response is None:
                last_data = existing_item
            else:
                last_data = response['json']
            on_update(self, last_data)
        elif auto_exit:
            self.exit_json(**self.json_output)
        else:
            if response is None:
                last_data = existing_item
            else:
                last_data = response['json']
            return last_data

    def create_or_update_if_needed(
        self, existing_item, new_item, endpoint=None, item_type='unknown', on_create=None, on_update=None, auto_exit=True
    ):
        # Remove boolean values of certain specific types
        # this is needed so that boolean fields will not get a false value when not provided
        for key in list(new_item.keys()):
            if key in self.argument_spec:
                param_spec = self.argument_spec[key]
                if 'type' in param_spec and param_spec['type'] == 'bool':
                    if new_item[key] is None:
                        new_item.pop(key)

        if existing_item:
            return self.update_if_needed(existing_item, new_item, item_type=item_type, on_update=on_update, auto_exit=auto_exit)
        else:
            return self.create_if_needed(
                existing_item, new_item, endpoint, on_create=on_create, item_type=item_type, auto_exit=auto_exit
            )
