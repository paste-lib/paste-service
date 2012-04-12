import hashlib
import datetime
import zlib
import gzip
import StringIO

from ..util import content_type_helper

from ..core.runtime import Runtime
env = Runtime.get().env


class Speed(object):
    @classmethod
    def get_content_type(cls, path):
        """

        :param path:
        :return:
        """
        return content_type_helper.filename_to_content_type(path)

    @classmethod
    def get_mime_type(cls, path):
        """

        :param path:
        :return:
        """
        content_type = content_type_helper.filename_to_content_type(path)
        return content_type.mime_type if content_type else None

    @classmethod
    def skip_network(cls, byte_count):
        """

        :param byte_count:
        :return:
        """
        return byte_count <= env.network_request_threshold

    @classmethod
    def header_caching(cls, path, set_header_func, last_modified=None, checksum=None, proxy_only=False,
                       browser_only=False, force=False):
        """

        :param path:
        :param set_header_func:
        :param last_modified:
        :param checksum:
        :param proxy_only:
        :param browser_only:
        :param force:
        :return:
        """
        if env.compile_mode:
            return

        content_type = content_type_helper.filename_to_content_type(filename=path)

        if force or content_type is not None:
            now = datetime.datetime.utcnow()
            expires = now + datetime.timedelta(weeks=(52 * 10))

            if browser_only == True or proxy_only == False:
                if checksum is None:
                    checksum = path

                if isinstance(last_modified, (int, long, float)):
                    last_modified = datetime.datetime.fromtimestamp(last_modified)
                elif last_modified is None or not isinstance(last_modified, datetime.datetime):
                    last_modified = now

                gmt_date_format = '%a, %d %b %Y %H:%M:%S GMT'
                set_header_func("Date", now.strftime(gmt_date_format))
                set_header_func('ETag', hashlib.md5(checksum + '-' + str(last_modified)).hexdigest())
                set_header_func("Expires", expires.strftime(gmt_date_format))
                set_header_func("ExpiresDefault", 'access plus 10 years')
                set_header_func("Last-Modified", last_modified.strftime(gmt_date_format))

            if proxy_only is True or browser_only is False:
                age = expires - datetime.datetime.utcnow()

                set_header_func("Cache-Control", "public, max-age=" + str(
                    (age.microseconds + (age.seconds + age.days * 24 * 3600) * 10 ** 6) / 10 ** 6))
                set_header_func('Vary', 'Accept-Encoding')

    @classmethod
    def browser_cache_headers(cls, path, set_header_func, last_modified, checksum=None, force=True):
        """

        :param path:
        :param set_header_func:
        :param last_modified:
        :param checksum:
        :param force:
        """
        Speed.header_caching(path=path, set_header_func=set_header_func, last_modified=last_modified, checksum=checksum,
                             browser_only=True, force=force)

    @classmethod
    def proxy_cache_headers(cls, path, set_header_func, last_modified, force=True):
        """

        :param path:
        :param set_header_func:
        :param last_modified:
        :param force:
        """
        Speed.header_caching(path=path, set_header_func=set_header_func, last_modified=last_modified, proxy_only=True,
                             force=force)

    @classmethod
    def compress_utf8(cls, response_body, set_header_func, path=None, skip_content_check=False, accept_encoding=''):
        """

        :param response_body:
        :param set_header_func:
        :param path:
        :param skip_content_check:
        :param accept_encoding:
        :return:
        """
        if not response_body:
            return response_body

        if ('gzip' in accept_encoding or 'deflate' in accept_encoding) and not Speed.skip_network(len(response_body)):
            content_type = content_type_helper.filename_to_content_type(
                filename=path) if not skip_content_check and path is not None else None

            if skip_content_check == True or (
                        content_type is not None and not content_type.is_image and not content_type.type == helpers._ContentType.Type.WOFF):
                for encoding in [encoding.strip().lower() for encoding in accept_encoding.split(',')]:
                    if encoding == 'deflate':
                        response_body = zlib.compress(response_body)[2:-4]
                        set_header_func('Content-Encoding', 'deflate')
                        break
                    elif encoding == 'gzip':
                        file_obj = StringIO.StringIO()
                        gzipped_file = gzip.GzipFile(fileobj=file_obj, mode='wb')
                        gzipped_file.write(response_body)
                        gzipped_file.close()
                        response_body = file_obj.getvalue()
                        set_header_func('Content-Encoding', 'gzip')
                        break

                set_header_func('Vary', 'Accept-Encoding')

        return response_body

    @classmethod
    def compress_image(cls, response_body, path=None, skip_content_check=False):
        """

        :param response_body:
        :param path:
        :param skip_content_check:
        :raise:
        """
        raise NotImplementedError

