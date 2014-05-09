"""Adapters for IPython msg spec versions."""

# Copyright (c) IPython Development Team.
# Distributed under the terms of the Modified BSD License.

import json

from IPython.core.release import kernel_protocol_version_info
from IPython.utils.tokenutil import token_at_cursor


def code_to_line(code, cursor_pos):
    """Turn a multiline code block and cursor position into a single line
    and new cursor position.
    
    For adapting complete_ and object_info_requests.
    """
    for line in code.splitlines(True):
        n = len(line)
        if cursor_pos > n:
            cursor_pos -= n
        else:
            break
    return line, cursor_pos


class Adapter(object):
    """Base class for adapting messages
    
    Override message_type(msg) methods to create adapters.
    """
    
    msg_type_map = {}
    
    def update_header(self, msg):
        return msg
    
    def update_metadata(self, msg):
        return msg
    
    def update_msg_type(self, msg):
        header = msg['header']
        msg_type = header['msg_type']
        if msg_type in self.msg_type_map:
            msg['msg_type'] = header['msg_type'] = self.msg_type_map[msg_type]
        return msg
    
    def handle_reply_status_error(msg):
        """This will be called *instead of* the regular handler
        
        on any reply with status != ok
        """
        return msg
    
    def __call__(self, msg):
        msg = self.update_header(msg)
        msg = self.update_metadata(msg)
        msg = self.update_msg_type(msg)
        header = msg['header']
        
        handler = getattr(self, header['msg_type'], None)
        if handler is None:
            return msg
        
        # handle status=error replies separately (no change, at present)
        if msg['content'].get('status', None) in {'error', 'aborted'}:
            return self.handle_reply_status_error(msg)
        return handler(msg)

def _version_str_to_list(version):
    """convert a version string to a list of ints
    
    non-int segments are excluded
    """
    v = []
    for part in version.split('.'):
        try:
            v.append(int(part))
        except ValueError:
            pass
    return v

class V5toV4(Adapter):
    """Adapt msg protocol v5 to v4"""
    
    version = '4.1'
    
    msg_type_map = {
        'execute_result' : 'pyout',
        'execute_input' : 'pyin',
        'error' : 'pyerr',
        'inspect_request' : 'object_info_request',
        'inspect_reply' : 'object_info_reply',
    }
    
    def update_header(self, msg):
        msg['header'].pop('version', None)
        return msg
    
    # shell channel
    
    def kernel_info_reply(self, msg):
        content = msg['content']
        content.pop('banner', None)
        for key in ('language_version', 'protocol_version'):
            if key in content:
                content[key] = _version_str_to_list(content[key])
        if content.pop('implementation', '') == 'ipython' \
            and 'implementation_version' in content:
            content['ipython_version'] = content.pop('implmentation_version')
        content.pop('implementation_version', None)
        content.setdefault("implmentation", content['language'])
        return msg
    
    def execute_request(self, msg):
        content = msg['content']
        content.setdefault('user_variables', [])
        return msg
    
    def execute_reply(self, msg):
        content = msg['content']
        content.setdefault('user_variables', {})
        # TODO: handle payloads
        return msg
    
    def complete_request(self, msg):
        content = msg['content']
        code = content['code']
        cursor_pos = content['cursor_pos']
        line, cursor_pos = code_to_line(code, cursor_pos)
        
        new_content = msg['content'] = {}
        new_content['text'] = ''
        new_content['line'] = line
        new_content['block'] = None
        new_content['cursor_pos'] = cursor_pos
        return msg
    
    def complete_reply(self, msg):
        content = msg['content']
        cursor_start = content.pop('cursor_start')
        cursor_end = content.pop('cursor_end')
        match_len = cursor_end - cursor_start
        content['matched_text'] = content['matches'][0][:match_len]
        content.pop('metadata', None)
        return msg
    
    def object_info_request(self, msg):
        content = msg['content']
        code = content['code']
        cursor_pos = content['cursor_pos']
        line, _ = code_to_line(code, cursor_pos)
        
        new_content = msg['content'] = {}
        new_content['oname'] = token_at_cursor(code, cursor_pos)
        new_content['detail_level'] = content['detail_level']
        return msg
    
    def object_info_reply(self, msg):
        """inspect_reply can't be easily backward compatible"""
        msg['content'] = {'found' : False, 'name' : 'unknown'}
        return msg
    
    # iopub channel
    
    def display_data(self, msg):
        content = msg['content']
        content.setdefault("source", "display")
        data = content['data']
        if 'application/json' in data:
            try:
                data['application/json'] = json.dumps(data['application/json'])
            except Exception:
                # warn?
                pass
        return msg
    
    # stdin channel
    
    def input_request(self, msg):
        msg['content'].pop('password', None)
        return msg


class V4toV5(Adapter):
    """Convert msg spec V4 to V5"""
    version = '5.0'
    
    # invert message renames above
    msg_type_map = {v:k for k,v in V5toV4.msg_type_map.items()}
    
    def update_header(self, msg):
        msg['header']['version'] = self.version
        return msg
    
    # shell channel
    
    def kernel_info_reply(self, msg):
        content = msg['content']
        for key in ('language_version', 'protocol_version', 'ipython_version'):
            if key in content:
                content[key] = ".".join(map(str, content[key]))
        
        if content['language'].startswith('python') and 'ipython_version' in content:
            content['implementation'] = 'ipython'
            content['implementation_version'] = content.pop('ipython_version')
        
        content['banner'] = ''
        return msg
    
    def execute_request(self, msg):
        content = msg['content']
        user_variables = content.pop('user_variables', [])
        user_expressions = content.setdefault('user_expressions', {})
        for v in user_variables:
            user_expressions[v] = v
        return msg
    
    def execute_reply(self, msg):
        content = msg['content']
        user_expressions = content.setdefault('user_expressions', {})
        user_variables = content.pop('user_variables', {})
        if user_variables:
            user_expressions.update(user_variables)
        return msg
    
    def complete_request(self, msg):
        old_content = msg['content']
        
        new_content = msg['content'] = {}
        new_content['code'] = old_content['line']
        new_content['cursor_pos'] = old_content['cursor_pos']
        return msg
    
    def complete_reply(self, msg):
        # complete_reply needs more context than we have to get cursor_start and end.
        # use special value of `-1` to indicate to frontend that it should be at
        # the current cursor position.
        content = msg['content']
        new_content = msg['content'] = {'status' : 'ok'}
        new_content['matches'] = content['matches']
        new_content['cursor_start'] = -len(content['matched_text'])
        new_content['cursor_end'] = None
        new_content['metadata'] = {}
        return msg
    
    def inspect_request(self, msg):
        content = msg['content']
        name = content['oname']
        
        new_content = msg['content'] = {}
        new_content['code'] = name
        new_content['cursor_pos'] = len(name)
        new_content['detail_level'] = content['detail_level']
        return msg
    
    def inspect_reply(self, msg):
        """inspect_reply can't be easily backward compatible"""
        content = msg['content']
        new_content = msg['content'] = {'status' : 'ok'}
        found = new_content['found'] = content['found']
        new_content['name'] = content['name']
        new_content['data'] = data = {}
        new_content['metadata'] = {}
        if found:
            lines = []
            for key in ('call_def', 'init_definition', 'definition'):
                if content.get(key, False):
                    lines.append(content[key])
                    break
            for key in ('call_docstring', 'init_docstring', 'docstring'):
                if content.get(key, False):
                    lines.append(content[key])
                    break
            if not lines:
                lines.append("<empty docstring>")
            data['text/plain'] = '\n'.join(lines)
        return msg
    
    # iopub channel
    
    def display_data(self, msg):
        content = msg['content']
        content.pop("source", None)
        data = content['data']
        if 'application/json' in data:
            try:
                data['application/json'] = json.loads(data['application/json'])
            except Exception:
                # warn?
                pass
        return msg
    
    # stdin channel
    
    def input_request(self, msg):
        msg['content'].setdefault('password', False)
        return msg
    


def adapt(msg, to_version=kernel_protocol_version_info[0]):
    """Adapt a single message to a target version
    
    Parameters
    ----------
    
    msg : dict
        An IPython message.
    to_version : int, optional
        The target major version.
        If unspecified, adapt to the current version for IPython.
    
    Returns
    -------
    
    msg : dict
        An IPython message appropriate in the new version.
    """
    header = msg['header']
    if 'version' in header:
        from_version = int(header['version'].split('.')[0])
    else:
        # assume last version before adding the key to the header
        from_version = 4
    adapter = adapters.get((from_version, to_version), None)
    if adapter is None:
        return msg
    return adapter(msg)


# one adapter per major version from,to
adapters = {
    (5,4) : V5toV4(),
    (4,5) : V4toV5(),
}