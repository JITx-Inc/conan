import json
import os
import platform

from conan.tools import CONAN_TOOLCHAIN_ARGS_FILE
from conan.tools.cmake.utils import is_multi_configuration
from conan.tools.gnu.make import make_jobs_cmd_line_arg
from conan.tools.meson.meson import ninja_jobs_cmd_line_arg
from conan.tools.microsoft.msbuild import msbuild_verbosity_cmd_line_arg, \
    msbuild_max_cpu_count_cmd_line_arg
from conans.client import tools
from conans.client.tools.files import chdir
from conans.client.tools.oss import cpu_count, args_to_string
from conans.errors import ConanException
from conans.util.files import mkdir, load


def _validate_recipe(conanfile):
    forbidden_generators = ["cmake", "cmake_multi"]
    if any(it in conanfile.generators for it in forbidden_generators):
        raise ConanException("Usage of toolchain is only supported with 'cmake_find_package'"
                             " or 'cmake_find_package_multi' generators")


def _cmake_cmd_line_args(conanfile, generator, parallel):
    args = []
    if not generator:
        return args

    # Arguments related to parallel
    if parallel:
        if "Makefiles" in generator and "NMake" not in generator:
            njobs = make_jobs_cmd_line_arg(conanfile)
            if njobs:
                args.append(njobs)

        if "Ninja" in generator and "NMake" not in generator:
            njobs = ninja_jobs_cmd_line_arg(conanfile)
            if njobs:
                args.append(njobs)

        if "Visual Studio" in generator:
            max_cpu_count = msbuild_max_cpu_count_cmd_line_arg(conanfile)
            if max_cpu_count:
                args.append(max_cpu_count)

    # Arguments for verbosity
    if "Visual Studio" in generator:
        verbosity = msbuild_verbosity_cmd_line_arg(conanfile)
        if verbosity:
            args.append(verbosity)

    return args


class CMake(object):
    """ CMake helper to use together with the toolchain feature. It implements a very simple
    wrapper to call the cmake executable, but without passing compile flags, preprocessor
    definitions... all that is set by the toolchain. Only the generator and the CMAKE_TOOLCHAIN_FILE
    are passed to the command line, plus the ``--config Release`` for builds in multi-config
    """

    def __init__(self, conanfile, parallel=True):
        _validate_recipe(conanfile)

        # Store a reference to useful data
        self._conanfile = conanfile
        self._parallel = parallel
        self._generator = None

        args_file = os.path.join(self._conanfile.generators_folder, CONAN_TOOLCHAIN_ARGS_FILE)
        if os.path.exists(args_file):
            json_args = json.loads(load(args_file))
            self._generator = json_args.get("cmake_generator")
            self._toolchain_file = json_args.get("cmake_toolchain_file")

        self._cmake_program = "cmake"  # Path to CMake should be handled by environment

    def configure(self, source_folder=None):
        # TODO: environment?
        if not self._conanfile.should_configure:
            return

        source = self._conanfile.source_folder
        if source_folder:
            source = os.path.join(self._conanfile.source_folder, source_folder)

        build_folder = self._conanfile.build_folder
        generator_folder = self._conanfile.generators_folder

        mkdir(build_folder)

        arg_list = [self._cmake_program]
        if self._generator:
            arg_list.append('-G "{}"'.format(self._generator))
        if self._toolchain_file:
            if os.path.isabs(self._toolchain_file):
                toolpath = self._toolchain_file
            else:
                toolpath = os.path.join(generator_folder, self._toolchain_file)
            arg_list.append('-DCMAKE_TOOLCHAIN_FILE="{}"'.format(toolpath.replace("\\", "/")))
        if self._conanfile.package_folder:
            pkg_folder = self._conanfile.package_folder.replace("\\", "/")
            arg_list.append('-DCMAKE_INSTALL_PREFIX="{}"'.format(pkg_folder))
        if platform.system() == "Windows" and self._generator == "MinGW Makefiles":
            arg_list.append('-DCMAKE_SH="CMAKE_SH-NOTFOUND"')
        arg_list.append('"{}"'.format(source))

        command = " ".join(arg_list)
        self._conanfile.output.info("CMake command: %s" % command)
        with chdir(build_folder):
            self._conanfile.run(command)

    def _build(self, build_type=None, target=None):
        bf = self._conanfile.build_folder
        is_multi = is_multi_configuration(self._generator)
        if build_type and not is_multi:
            self._conanfile.output.error("Don't specify 'build_type' at build time for "
                                         "single-config build systems")

        bt = build_type or self._conanfile.settings.get_safe("build_type")
        if not bt:
            raise ConanException("build_type setting should be defined.")
        build_config = "--config {}".format(bt) if bt and is_multi else ""

        args = []
        if target is not None:
            args = ["--target", target]

        cmd_line_args = _cmake_cmd_line_args(self._conanfile, self._generator, self._parallel)
        if cmd_line_args:
            args += ['--'] + cmd_line_args

        arg_list = [args_to_string([bf]), build_config, args_to_string(args)]
        arg_list = " ".join(filter(None, arg_list))
        command = "%s --build %s" % (self._cmake_program, arg_list)
        self._conanfile.output.info("CMake command: %s" % command)
        self._conanfile.run(command)

    def build(self, build_type=None, target=None):
        if not self._conanfile.should_build:
            return
        self._build(build_type, target)

    def install(self, build_type=None):
        if not self._conanfile.should_install:
            return
        mkdir(self._conanfile.package_folder)
        self._build(build_type=build_type, target="install")

    def test(self, build_type=None, target=None, output_on_failure=False):
        if not self._conanfile.should_test:
            return
        if self._conanfile.conf["tools.build:skip_test"]:
            return
        if not target:
            is_multi = is_multi_configuration(self._generator)
            target = "RUN_TESTS" if is_multi else "test"

        env = {'CTEST_OUTPUT_ON_FAILURE': '1' if output_on_failure else '0'}
        if self._parallel:
            env['CTEST_PARALLEL_LEVEL'] = str(cpu_count(self._conanfile.output))
        with tools.environment_append(env):
            self._build(build_type=build_type, target=target)
