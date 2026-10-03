"""Explicit network modes shared by orchestration and Ansible rendering."""


def network_profile(mode='dual-stack'):
    if mode not in ('dual-stack', 'ipv4'):
        raise ValueError('network_mode must be dual-stack or ipv4')
    dual = mode == 'dual-stack'
    return {'mode': mode, 'ipv6': dual, 'versions': [6, 4] if dual else [4],
            'families': ['IPv6', 'IPv4'] if dual else ['IPv4'],
            'primary': 'IPv6' if dual else 'IPv4',
            'policy': 'RequireDualStack' if dual else 'SingleStack',
            'bind_address': '::' if dual else '0.0.0.0',
            'loopback': '::1' if dual else '127.0.0.1',
            'identity': 'dual-stack-ipv6-primary' if dual else 'ipv4-only'}


def node_addresses(env):
    return ([env['node_ip'], env['node_ipv4']] if network_profile(env.get('network_mode', 'dual-stack'))['ipv6']
            else [env['node_ip']])
