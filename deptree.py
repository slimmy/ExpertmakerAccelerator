from traceback import print_exc
from datetime import datetime, date, time, timedelta

from extras import OptionEnum, OptionEnumValue, OptionString, OptionDefault, JobWithFile, typing_conv

class OptionException(Exception):
    pass

_date_types = (datetime, date, time, timedelta)

class DepTree:

    def __init__(self, methods, setup):
        tree = methods.new_deptree(setup.method)
        self.methods = methods
        self.top_method = setup.method
        self.tree = tree
        self.add_flags({'make' : False, 'link' : False, })
        seen = set()
        for method, data in self.tree.iteritems():
            seen.add(method)
            data['params'] = {method: setup.params[method]}
        unmatched = {method: params for method, params in setup.params.iteritems() if method not in seen}
        if unmatched:
            from extras import json_encode
            print "DepTree Warning:  Unmatched options remain: " + json_encode(unmatched)
        def collect(method):
            # All methods that method depend on
            for child in tree[method]['dep']:
                yield child
                for method in collect(child):
                    yield method
        # This probably updates some with the same data several times,
        # but this is cheap (key: dictref updates, nothing more.)
        for method, data in self.tree.iteritems():
            for submethod in set(collect(method)):
                data['params'].update(tree[submethod]['params'])
        self._fix_options(False)
        self._fix_jobids('jobids')
        self._fix_jobids('datasets')

    def add_flags(self, flags):
        uid = 0
        for x, y in self.tree.items():
            y.update(flags)
            y.update({'uid' : uid, })
            uid += 1

    def get_reqlist(self):
        for method, data in self.tree.items():
            full_params = {}
            for submethod, given_params in data['params'].iteritems():
                params = {k: dict(v) for k, v in self.methods.params[submethod].defaults.iteritems()}
                for k, v in given_params.iteritems():
                    params[k].update(v)
                full_params[submethod] = params
            yield method, data['uid'], self.methods.params2optset(full_params)

    def fill_in_default_options(self):
        self._fix_options(True)

    def _fix_jobids(self, key):
        for method, data in self.tree.iteritems():
            method_params = data['params'][method]
            data = method_params[key]
            method_wants = self.methods.params[method][key]
            old_style = self.methods.params[method].old_style
            res = {}
            for jobid_name in method_wants:
                if old_style:
                    assert isinstance(jobid_name, str), 'Old style input_%s accepts only strings.' % (key,)
                if isinstance(jobid_name, str):
                    value = data.get(jobid_name)
                    assert value is None or isinstance(value, str), 'Input %s on %s not a string as required' % (jobid_name, method,)
                elif isinstance(jobid_name, list):
                    if len(jobid_name) != 1 or not isinstance(jobid_name[0], str):
                        raise OptionException('Bad %s item on %s: %s' % (key, method, repr(jobid_name),))
                    jobid_name = jobid_name[0]
                    value = data.get(jobid_name)
                    if value:
                        if isinstance(value, str):
                            value = [e.strip() for e in value.split(',')]
                    else:
                        value = []
                    assert isinstance(value, list), 'Input %s on %s not a list or string as required' % (jobid_name, method,)
                else:
                    raise OptionException('%s item of unknown type %s on %s: %s' % (key, type(jobid_name), method, repr(jobid_name),))
                if value is not None or not old_style:
                    res[jobid_name] = value
            method_params[key] = res
            spill = set(data) - set(res)
            if spill:
                raise OptionException('Unknown %s on %s: %s' % (key, method, ', '.join(sorted(spill)),))

    def _fix_options(self, fill_in):
        for method, data in self.tree.iteritems():
            data = data['params'][method]
            options = self.methods.params[method].options
            res_options = {}
            def convert(default_v, v):
                if default_v is None or v is None:
                    if default_v is OptionString:
                        raise OptionException('Option %s on method %s requires a non-empty string value' % (k, method,))
                    if hasattr(default_v, '_valid') and v not in default_v._valid:
                        raise OptionException('Option %s on method %s requires a value in %s' % (k, method, default_v._valid,))
                    if isinstance(default_v, OptionDefault):
                        v = default_v.default
                    return v
                if isinstance(default_v, OptionDefault):
                    default_v = default_v.value
                if isinstance(default_v, dict) and isinstance(v, dict):
                    if default_v:
                        sample_v = default_v.values()[0]
                        for chk_v in default_v.itervalues():
                            assert isinstance(chk_v, type(sample_v))
                        return {k: convert(sample_v, v) for k, v in v.iteritems()}
                    else:
                        return v
                if isinstance(default_v, (list, set, tuple,)) and isinstance(v, (str, list, set, tuple,)):
                    if isinstance(v, str):
                        v = (e.strip() for e in v.split(','))
                    if default_v:
                        sample_v = list(default_v)[0]
                        for chk_v in default_v:
                            assert isinstance(chk_v, type(sample_v))
                        v = (convert(sample_v, e) for e in v)
                    return type(default_v)(v)
                if isinstance(default_v, (OptionEnum, OptionEnumValue,)):
                    if not (v or None) in default_v._valid:
                        ok = False
                        for cand_prefix in default_v._prefixes:
                            if v.startswith(cand_prefix):
                                ok = True
                                break
                        if not ok:
                            raise OptionException('%r not a permitted value for option %s on method %s (%s)' % (v, k, method, default_v._valid))
                    return v or None
                if isinstance(default_v, (str, int, float, long)) and isinstance(v, (str, int, float, long)):
                    if default_v is OptionString:
                        v = str(v)
                        if not v:
                            raise OptionException('Option %s on method %s requires a non-empty string value' % (k, method,))
                        return v
                    return type(default_v)(v)
                if (isinstance(default_v, type) and isinstance(v, default_v)) or isinstance(v, type(default_v)):
                    return v
                if isinstance(default_v, bool) and isinstance(v, (str, int)):
                    lv = str(v).lower()
                    if lv in ('true', '1', 't', 'yes', 'on',):
                        return True
                    if lv in ('false', '0', 'f', 'no', 'off', '',):
                        return False
                if isinstance(default_v, _date_types):
                    default_v = type(default_v)
                if default_v in _date_types:
                    try:
                        return typing_conv[default_v.__name__](v)
                    except Exception:
                        raise OptionException('Failed to convert option %s %r to %s on method %s' % (k, v, default_v, method,))
                if isinstance(v, str) and not v:
                    return type(default_v)()
                if isinstance(default_v, type): # JobWithFile or similar
                    default_v = default_v()
                if isinstance(default_v, JobWithFile):
                    defaults = type(default_v).__new__.__defaults__
                    if not isinstance(v, (list, tuple,)) or len(v) > len(defaults):
                        raise OptionException('Option %s (%r) on method %s is not %s compatible' % (k, v, method, type(default_v)))
                    v = tuple(v) + defaults[len(v):] # so all of default_v gets convert()ed.
                    v = [convert(dv, vv) for dv, vv in zip(default_v, v)]
                    return type(default_v)(*v)
                raise OptionException('Failed to convert option %s of %s to %s on method %s' % (k, type(v), type(default_v), method,))
            if self.methods.params[method].old_style:
                res_options.update(data['options'])
                data['options'] = res_options
            else:
                for k, v in data['options'].iteritems():
                    if k in options:
                        try:
                            res_options[k] = convert(options[k], v)
                        except OptionException:
                            raise
                        except Exception:
                            print_exc()
                            raise OptionException('Failed to convert option %s on method %s' % (k, method,))
                    else:
                        raise OptionException('Unknown option %s on method %s' % (k, method,))
            if fill_in:
                missing = set(options) - set(res_options)
                missing_required = missing & self.methods.params[method].required
                if missing_required:
                    raise OptionException('Missing required options {%s} on method %s' % (', '.join(sorted(missing_required)), method,))
                defaults = self.methods.params[method].defaults
                res_options.update({k: defaults.options[k] for k in missing})
            data['options'] = res_options

    def get_item_by_uid(self, uid):
        return filter(lambda x : x[1]['uid'] == uid, self.tree.items())[0][1]

    def set_link(self, uid, link):
        item = self.get_item_by_uid(uid)
        item['link'] = link

    def propagate_make(self):
        self._recursive_propagate_make(self.top_method)

    def _recursive_propagate_make(self, node):
        for child in self.tree[node]['dep']:
            self._recursive_propagate_make(child)
        self.tree[node]['make'] = (
            True in [self.tree[x]['make'] for x in self.tree[node]['dep'] if x]) or (
            not self.tree[node]['link'])

    def get_sorted_joblist(self):
        return sorted(self.tree.values(), key = lambda x : x['level'])

    def get_link(self, node):
        return self.tree[node]['link']


    def debugprint(self):
        for x, y in sorted(self.tree.items(), key = lambda x: -int(x[1]['level'])):
            print ' %15s' % x, 
            for k in y:
                print '%5s=%5s' % (k, y[k]),
        print
        
