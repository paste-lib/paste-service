#!/usr/bin/env python
import os
import re
import stat
import sys

sys.path.append(os.path.normpath(os.path.dirname(os.path.abspath(__file__)) + "/..") + os.sep)

import optparse
import types

import logging

log = logging.getLogger('paste')

from ..util import OrderedDict, content_type_helper

from ..core.runtime import Runtime
env = Runtime.get().env

from ..core import manifest

DEBUG_LAST_MODIFIED_URI = ''
TIMESTAMP_EXPR = re.compile(r'^[' + DEBUG_LAST_MODIFIED_URI + r'require0-9]+/')
VERSION_PREFIX = '+v'


def _ensure_file_extension(file_extension):
    if isinstance(file_extension, types.StringTypes) and not file_extension.startswith('.'):
        file_extension = ''.join(['.', file_extension])
    return file_extension


class _ModuleDependency(object):
    def __init__(self, name, version=None):
        super(_ModuleDependency, self).__init__()

        self.name = name.strip()
        self.version = float(version.strip()) if version else None
        self._last_modified = None
        self._latest_lm = None
        self._version_mismatch = None
        self._source_path = None

    def _initialize(self, module):
        self._latest_lm = module.last_modified
        self._removed = module.removed
        deserialized_module = next(
            (mv for mv in module.serialized_versions if self.version and mv.get('version') == self.version),
            None
        )
        if deserialized_module:
            module = module.deserialize(deserialized_module)

        self._last_modified = module.last_modified
        self._version_mismatch = self._removed or self._latest_lm != self._last_modified
        self._source_path = module.path

    def get_has_ver_mismatch(self, module):
        if self._version_mismatch is None and module:
            self._initialize(module)
        return self._version_mismatch

    def get_last_modified(self, module):
        if self._last_modified is None and module:
            self._initialize(module)
        return self._last_modified

    def get_source_path(self, module):
        if self._source_path is None and module:
            self._initialize(module)
        return self._source_path

    @classmethod
    def create(cls, dependency_name, version=None):
        if not version:
            version_match = re.search(r"\+v(?P<version>.+)", dependency_name)
            if version_match:
                version = version_match.group('version') or None
                if version:
                    dependency_name = dependency_name.replace(VERSION_PREFIX + version, '')
        return cls(dependency_name, version)


class Jammer:
    def __init__(self, dependencies=None, request_path=None, require_dependencies=False, content_type=None):
        """

        :param dependencies:
        :param request_path:
        :param require_dependencies:
        :param content_type:
        """
        self._content_type_manifest = None
        self._content_type_sorted_keys = None

        self.content_type = (content_type_helper.filename_to_content_type(
            _ensure_file_extension(content_type)
        ) or content_type or content_type_helper.filename_to_content_type(request_path))

        if not self.content_type:
            self.dependencies = OrderedDict()
            log.warning('no content type passed to Jammer!')

        self.uri_path_expr = re.compile(
            r"(?:(?P<last_modified>[0-9]+)/?)?(?P<dependencies>[^/]*)(?=" + self.content_type.file_extension + r")")

        if request_path and not dependencies:
            dependencies = self.parse_request_path_dependencies(request_path)

        if dependencies and request_path and not require_dependencies:

            # sometimes, we don't want to walk the tree but return exactly what is requested.
            # in this case an exact uri request.

            # step 1. after normalizing star dependencies, create an ordered dict according to
            # the uri patch or comma-sep dependencies passed

            # step 2. if the instance is not a result of a URI or if it is and there is no
            # version mismatch, re-order the dependencies
            exploded_dependencies = [_ModuleDependency.create(module_name)
                                     for module_name in self._normalize_star_token(dependencies.split(','))]
            ed_od = OrderedDict((d.name, d) for d in exploded_dependencies)
            ver_mismatch = next(
                (d_name for (d_name, d) in ed_od.iteritems()
                 if d.get_has_ver_mismatch(self.content_type_manifest.manifest.get(d_name))),
                None
            )

            # if there is a version mismatch with a URI path, we want to try and just fulfill
            # it the way it's been requested for backward compatibility
            if not request_path or (request_path and not ver_mismatch):
                # get all the possible names in the sorted manifest
                sorted_names = [name for (name, path, version) in self.content_type_manifest.sorted_deps]
                ed_od = OrderedDict(
                    (name, ed_od.get(name)) for name in sorted_names if name in ed_od
                )

                # attempt to back-fill any dependencies that may have been removed or changed
                if request_path:
                    ed_od.update(OrderedDict(
                        (d_name, d) for (d_name, d) in ed_od.iteritems() if d_name not in sorted_names)
                    )

            self.dependencies = ed_od

        elif dependencies:
            # walk the full tree of each dependency e.g. a "require" call

            # step 1. get all the modules from the normalized star dependencies result

            # step 2. for each iteration of step 1, union the current name to the dependencies
            # of the current name in the primer manifest
            exploded_dependencies = [
                _ModuleDependency.create(module_name)
                for module_name in self._normalize_star_token(dependencies.split(','))
            ]
            ed_dict = dict((d.name, d) for d in exploded_dependencies if d.name in self.content_type_manifest.manifest)
            missing_deps = [_ModuleDependency.create(name)
                            for (d_name, d) in ed_dict.iteritems()
                            for name in self.content_type_manifest.manifest.get(d_name).dependencies]
            ed_dict.update(dict((d.name, d) for d in missing_deps))

            self.dependencies = self._order_dependencies(ed_dict)

        else:
            # note: jammer can still work even if no dependecies are passed. the url property will simply return None
            self.dependencies = OrderedDict()
            log.debug('no dependencies passed to Jammer!')

        self._checksum = None
        self._uri = None
        self._contents = None
        self._last_modified = None
        self._byte_size = None

    def parse_request_path_dependencies(self, path):
        """

        :param path:
        :return:
        """
        return next((match.group('dependencies') for match in self.uri_path_expr.finditer(path) if
                     match.group('dependencies')), '')

    def parse_request_path_last_modified(self, path):
        """

        :param path:
        :return:
        """
        return next((match.group('last_modified') for match in self.uri_path_expr.finditer(path) if
                     match.group('dependencies')), '')

    def _order_dependencies(self, dependencies):
        ordered_dependencies = OrderedDict()
        for name, path, version in self.content_type_manifest.sorted_deps:
            if name in dependencies:
                dep = dependencies.get(name)
                dep.version = float(version) if version else None
                ordered_dependencies[name] = dep
        return ordered_dependencies

    def _normalize_star_token(self, dependencies):
        # step 1. find all the dependencies that are .* dependencies e.g. paste.*

        # step 2. in each iteration of step 1, match relevant children (e.g. paste.event, util, etc)
        # in the primer manifest
        star_deps = {}
        for i, dependency_name in enumerate(dependencies):
            if dependency_name.strip().endswith('.*'):
                dp_name = dependencies.pop(i).strip()[:-2]
                star_deps[i] = []

                for (name, module, version) in self.content_type_manifest.sorted_deps:
                    if name == dp_name or name.startswith(dp_name + '.'):
                        star_deps[i].append(name)

        for i, deps in star_deps.iteritems():
            dependencies = dependencies[:i] + deps + dependencies[i:]

        return dependencies

    def _set_debug_properties(self):
        dependencies_stats = [
            os.stat(d.get_source_path(self.content_type_manifest.manifest.get(d_name)))
            for (d_name, d) in self.dependencies.iteritems()
        ]
        self._last_modified = max(file_stat[stat.ST_MTIME] for file_stat in dependencies_stats)
        self._byte_size = sum(file_stat[stat.ST_SIZE] for file_stat in dependencies_stats)

    @property
    def content_type_manifest(self):
        # hook into the manifest
        """


        :return:
        """
        if self._content_type_manifest is None:
            self._content_type_manifest = manifest.get_content_type_manifest(self.content_type)
        return self._content_type_manifest

    @property
    def last_modified(self):
        """


        :return:
        """
        if not self._last_modified and self.dependencies:
            if self.is_debug:
                self._set_debug_properties()
            else:
                self._last_modified = max([
                    d.get_last_modified(self.content_type_manifest.manifest.get(d_name))
                    for (d_name, d) in self.dependencies.iteritems()
                ])
            log.debug('generated last_modified: %i' % self._last_modified)
        return self._last_modified

    @property
    def _checksum_format(self):
        return '{name}' if self.is_debug else '{name}{version_prefix}'

    @property
    def checksum(self):
        """


        :return:
        """
        if not self._checksum and self.dependencies:
            for d_name, d in self.dependencies.iteritems():
                if d.version is None:
                    d.version = self.content_type_manifest.manifest.get(d.name).version

            self._checksum = ','.join([
                self._checksum_format.format(name=d.name, version_prefix=VERSION_PREFIX + str(d.version))
                for (d_name, d) in self.dependencies.iteritems()
            ])
            log.debug('generated checksum: %s' % self._checksum)
        return self._checksum

    @property
    def uri(self):
        """


        :return:
        """
        if not self._uri and self.dependencies:
            self._uri = '%s%s%s%s' % (
                env.root_uri,
                ('%d/' % self.last_modified) if not self.is_debug else DEBUG_LAST_MODIFIED_URI,
                self.checksum,
                self.content_type.file_extension
            )
            log.debug('generated uri: %s' % self._uri)
        return self._uri

    @property
    def unjammed_uris(self):
        """


        :return:
        """
        return ['%s%s%s%s' % (
            env.root_uri,
            ('%d/' % self.last_modified) if not self.is_debug else DEBUG_LAST_MODIFIED_URI,
            self._checksum_format.format(name=d.name, version_prefix=VERSION_PREFIX + str(d.version)),
            self.content_type.file_extension
        ) for (d_name, d) in self.dependencies.iteritems()]

    @property
    def byte_size(self):
        """


        :return:
        """
        if not self._byte_size and self.dependencies:
            if self.is_debug:
                self._set_debug_properties()
            else:
                self._byte_size = sum(
                    primer_part.byte_size
                    for (primer_part_name, primer_part) in self.content_type_manifest.manifest.iteritems()
                    if primer_part_name in self.dependencies
                ) or 0
            log.debug('generated byte_size: %d' % self._byte_size)
        return self._byte_size

    @property
    def contents(self):
        """


        :return:
        """
        if not self._contents and self.dependencies:
            # potential fixme: responses *should* be cached via cloud front and url caching
            self._contents = ''.join([
                self.read_contents(
                    filename=d.get_source_path(self.content_type_manifest.manifest.get(d_name))
                ) for (d_name, d) in self.dependencies.iteritems()
            ])
            log.debug('generated contents: %s' % self._contents)
        return self._contents

    @property
    def is_debug(self):
        """


        :return:
        """
        return env.compile_mode

    def filter_loaded(self, loaded_deps):
        """

        :param loaded_deps:
        :return:
        """
        if not loaded_deps:
            loaded_deps = set()

        keys = set(self.dependencies.keys()) - loaded_deps
        self.dependencies = self._order_dependencies(
            dict((d.name, d) for (d_name, d) in self.dependencies.iteritems() if d.name in keys)
        )

        return set(self.dependencies.keys())

    def read_contents(self, filename):
        """

        :param filename:
        :return:
        """
        return self.content_type_manifest.primer.read_primed(filename)

    @classmethod
    def jam_filter_loaded(cls, file_extension, dependencies, loaded_deps=None):

        """

        :param file_extension:
        :param dependencies:
        :param loaded_deps:
        :return:
        """
        file_extension = _ensure_file_extension(file_extension)

        content_type = content_type_helper.filename_to_content_type(file_extension)
        # note: this is done by default on the client, but we need similar functionality here

        # find deps for request

        if not isinstance(dependencies, types.StringTypes):
            log.warning(
                'Dependencies of non-StringTypes passed. file_extension=%s; dependencies=%r; loaded_deps=%r' % (
                    file_extension,
                    dependencies,
                    loaded_deps
                )
            )
            dependencies = ''
        jammer = cls(
            content_type=content_type,
            dependencies=','.join(
                [request_part.strip()
                 for request_part in dependencies.split(',') if request_part.strip() not in loaded_deps]
            )
        )

        # if there are loaded dependencies, subtract them
        # add the new dependencies to the loaded set
        loaded_deps |= jammer.filter_loaded(loaded_deps)

        return jammer
