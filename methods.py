############################################################################
#                                                                          #
# Copyright (c) 2017 eBay Inc.                                             #
#                                                                          #
# Licensed under the Apache License, Version 2.0 (the "License");          #
# you may not use this file except in compliance with the License.         #
# You may obtain a copy of the License at                                  #
#                                                                          #
#  http://www.apache.org/licenses/LICENSE-2.0                              #
#                                                                          #
# Unless required by applicable law or agreed to in writing, software      #
# distributed under the License is distributed on an "AS IS" BASIS,        #
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. #
# See the License for the specific language governing permissions and      #
# limitations under the License.                                           #
#                                                                          #
############################################################################

from __future__ import print_function
from __future__ import division

import os
import datetime
from time import time
from collections import defaultdict

from compat import iteritems, itervalues, first_value, NoneType, unicode, long

from extras import DotDict, OptionString, OptionEnum, OptionDefault, RequiredOption
from runner import new_runners
from setupfile import _sorted_set


class MethodLoadException(Exception):
	def __init__(self, lst):
		Exception.__init__(self, 'Failed to load ' + ', '.join(lst))
		self.module_list = lst

class Methods(object):
	def __init__(self, package_list, configfilename):
		self.package_list = package_list
		self.db = {}
		for package in self.package_list:
			if not os.path.lexists(package):
				src = os.path.join("..", package)
				if os.path.exists(src):
					os.symlink(src, package)
			tmp = read_method_conf(os.path.join(package, configfilename))
			for x in tmp:
				if x in self.db:
					print("METHOD:  ERROR, method \"%s\" defined both in \"%s\" and \"%s\"!" % (
						x, package, self.db[x]['package']))
					exit(1)
			for x in tmp.values():
				x['package'] = os.path.basename(package)
			self.db.update(tmp)
		# build dependency tree for all methods
		self.deptree = {}
		for method in self.db:
			self.deptree[method] = self._build_dep_tree(method, tree={})
		self.link = {k: v.get('link') for k, v in iteritems(self.db)}

	def _build_dep_tree(self, method, tree={}):
		if method not in self.db:
			print("METHOD:  Error, no such method exists: \"%s\"" % method)
			exit(1)
		dependencies = self.db[method].get('dep', [])
		tree.setdefault(method, {'dep' : dependencies, 'level' : -1, 'method' : method})
		if not dependencies:
			tree[method]['level'] = 0
		else:
			for dep in dependencies:
				self._build_dep_tree(dep, tree=tree)
				tree[method]['level'] = max(
					tree[method]['level'],
					tree[dep]['level']+1,
				)
		return tree

	def new_deptree(self, top_method):
		return self._build_dep_tree(top_method, tree={})


# Collect information on methods
class SubMethods(Methods):
	def __init__(self, package_list, configfilename, daemon_config):
		super(SubMethods, self).__init__(package_list, configfilename)
		t0 = time()
		self.runners = new_runners(daemon_config)
		per_runner = defaultdict(list)
		for key, val in iteritems(self.db):
			package = val['package']
			per_runner[val['version']].append((package, key))
		warnings = []
		failed = []
		self.hash = {}
		self.params = {}
		self.typing = {}
		for version, data in iteritems(per_runner):
			runner = self.runners.get(version)
			if not runner:
				msg = '%%s.%%s (unconfigured version %s)' % (version)
				failed.extend(msg % t for t in sorted(data))
				continue
			w, f, h, p = runner.load_methods(data)
			warnings.extend(w)
			failed.extend(f)
			self.hash.update(h)
			self.params.update(p)
		for key, params in iteritems(self.params):
			self.typing[key] = options2typing(key, params.options)
			params.defaults = params2defaults(params)
			params.required = options2required(params.options)
		def prt(a, prefix):
			maxlen = (max(len(e) for e in a) + len(prefix))
			line = '=' * maxlen
			print()
			print(line)
			for e in sorted(a):
				msg = prefix + e
				print(msg + ' ' * (maxlen - len(msg)))
			print(line)
			print()
		if warnings:
			prt(warnings, 'WARNING: ')
		if failed:
			print('\033[47;31;1m')
			prt(failed, 'FAILED to import ')
			print('\033[m')
			raise MethodLoadException(failed)
		print("Updated %d methods on %d runners in %.1f seconds" % (
		      len(self.hash), len(per_runner), time() - t0,
		     ))

	def params2optset(self, params):
		optset = set()
		for optmethod, method_params in iteritems(params):
			for group, d in iteritems(method_params):
				filled_in = dict(self.params[optmethod].defaults[group])
				filled_in.update(d)
				for optname, optval in iteritems(filled_in):
					optset.add('%s %s-%s %s' % (optmethod, group, optname, _reprify(optval),))
		return optset

def _reprify(o):
	if isinstance(o, OptionDefault):
		o = o.default
	if isinstance(o, (bytes, str, int, float, long, bool, NoneType)):
		return repr(o)
	if isinstance(o, unicode):
		# not reachable in PY3, the above "str" matches
		return repr(o.encode('utf-8'))
	if isinstance(o, set):
		return '[%s]' % (', '.join(map(_reprify, _sorted_set(o))),)
	if isinstance(o, (list, tuple)):
		return '[%s]' % (', '.join(map(_reprify, o)),)
	if isinstance(o, dict):
		return '{%s}' % (', '.join('%s: %s' % (_reprify(k), _reprify(v),) for k, v in sorted(iteritems(o))),)
	if isinstance(o, (datetime.datetime, datetime.date, datetime.time, datetime.timedelta,)):
		return str(o)
	raise Exception('Unhandled %s in dependency resolution' % (type(o),))



def params2defaults(params):
	d = DotDict()
	for key in ('datasets', 'jobids',):
		r = {}
		for v in params[key]:
			if isinstance(v, list):
				r[v[0]] = []
			else:
				r[v] = None
		d[key] = r
	def fixup(item):
		if isinstance(item, dict):
			d = {k: fixup(v) for k, v in iteritems(item)}
			if len(d) == 1 and first_value(d) is None and first_value(item) is not None:
				return {}
			return d
		if isinstance(item, (list, tuple, set,)):
			l = [fixup(v) for v in item]
			if l == [None] and list(item) != [None]:
				l = []
			return type(item)(l)
		if isinstance(item, type):
			return None
		assert isinstance(item, (bytes, unicode, int, float, long, bool, OptionEnum, NoneType, datetime.datetime, datetime.date, datetime.time, datetime.timedelta)), type(item)
		return item
	def fixup0(item):
		if isinstance(item, RequiredOption):
			item = item.value
		if isinstance(item, OptionDefault):
			item = item.default
		return fixup(item)
	d.options = {k: fixup0(v) for k, v in iteritems(params.options)}
	return d


def options2required(options):
	res = set()
	def chk(key, value):
		if value is OptionString or isinstance(value, RequiredOption):
			res.add(key)
		elif isinstance(value, OptionEnum):
			if None not in value._valid:
				res.add(key)
		elif isinstance(value, dict):
			for v in itervalues(value):
				chk(key, v)
		elif isinstance(value, (list, tuple, set,)):
			for v in value:
				chk(key, v)
	for key, value in iteritems(options):
		chk(key, value)
	return res


def options2typing(method, options):
	from extras import JobWithFile
	res = {}
	def value2spec(value):
		if isinstance(value, list):
			if not value:
				return
			fmt = '[%s]'
			value = value[0]
		else:
			fmt = '%s'
		typ = None
		if value is JobWithFile or isinstance(value, JobWithFile):
			typ = 'JobWithFile'
		elif isinstance(value, set):
			typ = 'set'
		elif value in (datetime.datetime, datetime.date, datetime.time, datetime.timedelta,):
			typ = value.__name__
		elif isinstance(value, (datetime.datetime, datetime.date, datetime.time, datetime.timedelta,)):
			typ = type(value).__name__
		if typ:
			return fmt % (typ,)
	def collect(key, value, path=''):
		path = "%s/%s" % (path, key,)
		if isinstance(value, dict):
			for v in itervalues(value):
				collect('*', v, path)
			return
		spec = value2spec(value)
		assert res.get(path, spec) == spec, 'Method %s has incompatible types in options%s' % (method, path,)
		res[path] = spec
	for k, v in iteritems(options):
		collect(k, v)
	# reverse by key len, so something inside a dict always comes before
	# the dict itself. (We don't currently have any dict-like types, but we
	# might later.)
	return sorted(([k[1:], v] for k, v in iteritems(res) if v), key=lambda i: -len(i[0]))


def read_method_conf(filename):
	""" read and parse the methods.conf file """
	db = {}
	with open(filename) as fh:
		for lineno, line in enumerate(fh, 1):
			data = line.split('#')[0].split()
			if not data:
				continue
			method = data.pop(0)
			try:
				version = data.pop(0)
			except IndexError:
				version = 'py'
			if not version.startswith('py') or data:
				raise Exception('Trailing garbage on %s:%d: %s' % (filename, lineno, line,))
			db[method] = DotDict(version=version)
	return db
