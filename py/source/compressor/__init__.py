import os
import subprocess

import scss

_compressor_dir = os.path.dirname(os.path.abspath(__file__))

_yui_compressor_args = 'java -jar %s' % (os.path.join(_compressor_dir, 'yuicompressor-2.4.7.jar'))
_closure_compressor_args = 'java -jar %s' % (os.path.join(_compressor_dir, 'closure-compiler.jar'))
_html_compressor_args = 'java -jar %s' % (os.path.join(_compressor_dir, 'htmlcompressor-1.5.2.jar'))

HTML = 'html'
JS = 'js'
CSS = 'css'


def compress(content, file_type=None, arguments='', **kwargs):
    """

    :param content:
    :param file_type:
    :param arguments:
    :param kwargs:
    :return:
    """
    if file_type is None:
        print 'NO FILE TYPE. YUI COMPRESSOR WILL NOT RUN'
        return content

    if file_type.lower() == JS:
        compressor = _closure_compressor_args
    elif file_type.lower() == CSS:

        opts = {
            'compress': ('--compress' in arguments),
            'compress_short_colors': 0
        }
        if 'load_paths' in kwargs:
            opts['load_paths'] = kwargs.get('load_paths')

        _scss = scss.Scss(scss_opts=opts)
        output = _scss.compile(content)

        return output
    else:
        compressor = _html_compressor_args
        arguments = '--type=%s %s' % (file_type, arguments)

    command = 'nice %s %s' % (compressor, arguments)

    p = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    p.stdin.write(content)
    p.stdin.close()

    content = p.stdout.read()
    p.stdout.close()

    return content