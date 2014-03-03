import sys
from unittest import TestCase

import mock
from nose.tools import eq_, raises

from . import Receiver, Sender
from .exc import (AlreadyProcessed,
                  BadHeaderValue,
                  CredentialsLookupError,
                  InvalidCredentials,
                  MacMismatch,
                  MisComputedContentHash,
                  TokenExpired)
from .util import (parse_authorization_header,
                   utc_now,
                   validate_credentials)


class Base(TestCase):

    def setUp(self):
        self.credentials = {
            'id': 'my-hawk-id',
            'key': 'my hAwK sekret',
            'algorithm': 'sha256',
        }

        # This callable might be replaced by tests.
        def seen_nonce(nonce, ts):
            return False
        self.seen_nonce = seen_nonce

    def credentials_map(self, id):
        # Pretend this is doing something more interesting like looking up
        # a credentials by ID in a database.
        if self.credentials['id'] != id:
            raise LookupError('No credentialsuration for Hawk ID {id}'
                              .format(id=id))
        return self.credentials


class TestConfig(Base):

    @raises(InvalidCredentials)
    def test_no_id(self):
        c = self.credentials.copy()
        del c['id']
        validate_credentials(c)

    @raises(InvalidCredentials)
    def test_no_key(self):
        c = self.credentials.copy()
        del c['key']
        validate_credentials(c)

    @raises(InvalidCredentials)
    def test_no_algo(self):
        c = self.credentials.copy()
        del c['algorithm']
        validate_credentials(c)

    @raises(InvalidCredentials)
    def test_no_credentials(self):
        validate_credentials(None)


class TestSender(Base):

    def setUp(self):
        super(TestSender, self).setUp()
        self.url = 'http://site.com/foo?bar=1'

    def Sender(self, method='GET', **kw):
        credentials = kw.pop('credentials', self.credentials)
        kw.setdefault('content', '')
        kw.setdefault('content_type', '')
        sender = Sender(credentials, self.url, method, **kw)
        return sender

    def receive(self, request_header, url=None, method='GET', **kw):
        credentials_map = kw.pop('credentials_map', self.credentials_map)
        kw.setdefault('content', '')
        kw.setdefault('content_type', '')
        kw.setdefault('seen_nonce', self.seen_nonce)
        return Receiver(credentials_map, request_header,
                        url or self.url, method, **kw)

    def test_get_ok(self):
        method = 'GET'
        sn = self.Sender(method=method)
        self.receive(sn.request_header, method=method)

    def test_post_ok(self):
        method = 'POST'
        sn = self.Sender(method=method)
        self.receive(sn.request_header, method=method)

    def test_post_content_ok(self):
        method = 'POST'
        content = 'foo=bar&baz=2'
        sn = self.Sender(method=method, content=content)
        self.receive(sn.request_header, method=method, content=content)

    def test_post_content_type_ok(self):
        method = 'POST'
        content = '{"bar": "foobs"}'
        content_type = 'application/json'
        sn = self.Sender(method=method, content=content,
                         content_type=content_type)
        self.receive(sn.request_header, method=method, content=content,
                     content_type=content_type)

    def test_post_content_type_with_trailing_charset(self):
        method = 'POST'
        content = '{"bar": "foobs"}'
        content_type = 'application/json; charset=utf8'
        sn = self.Sender(method=method, content=content,
                         content_type=content_type)
        self.receive(sn.request_header, method=method, content=content,
                     content_type='application/json; charset=other')

    @raises(ValueError)
    def test_missing_payload_details(self):
        self.Sender(method='POST', content=None, content_type=None)

    def test_skip_payload_hashing(self):
        method = 'POST'
        content = '{"bar": "foobs"}'
        content_type = 'application/json'
        sn = self.Sender(method=method, content=None, content_type=None,
                         always_hash_content=False)
        self.receive(sn.request_header, method=method, content=content,
                     content_type=content_type,
                     accept_untrusted_content=True)

    @raises(ValueError)
    def test_cannot_skip_content_only(self):
        self.Sender(method='POST', content=None,
                    content_type='application/json')

    @raises(ValueError)
    def test_cannot_skip_content_type_only(self):
        self.Sender(method='POST', content='{"foo": "bar"}',
                    content_type=None)

    @raises(MacMismatch)
    def test_tamper_with_host(self):
        sn = self.Sender()
        self.receive(sn.request_header, url='http://TAMPERED-WITH.com')

    @raises(MacMismatch)
    def test_tamper_with_method(self):
        sn = self.Sender(method='GET')
        self.receive(sn.request_header, method='POST')

    @raises(MacMismatch)
    def test_tamper_with_path(self):
        sn = self.Sender()
        self.receive(sn.request_header,
                     url='http://site.com/TAMPERED?bar=1')

    @raises(MacMismatch)
    def test_tamper_with_query(self):
        sn = self.Sender()
        self.receive(sn.request_header,
                     url='http://site.com/foo?bar=TAMPERED')

    @raises(MacMismatch)
    def test_tamper_with_scheme(self):
        sn = self.Sender()
        self.receive(sn.request_header, url='https://site.com/foo?bar=1')

    @raises(MacMismatch)
    def test_tamper_with_port(self):
        sn = self.Sender()
        self.receive(sn.request_header,
                     url='http://site.com:8000/foo?bar=1')

    @raises(MacMismatch)
    def test_tamper_with_content(self):
        sn = self.Sender(method='POST')
        self.receive(sn.request_header, content='stuff=nope')

    @raises(MacMismatch)
    def test_tamper_with_content_type(self):
        sn = self.Sender(method='POST')
        self.receive(sn.request_header, content_type='application/json')

    @raises(AlreadyProcessed)
    def test_nonce_fail(self):

        def seen_nonce(nonce, ts):
            return True

        sn = self.Sender()

        self.receive(sn.request_header, seen_nonce=seen_nonce)

    def test_nonce_ok(self):

        def seen_nonce(nonce, ts):
            return False

        sn = self.Sender(seen_nonce=seen_nonce)
        self.receive(sn.request_header)

    @raises(TokenExpired)
    def test_expired_ts(self):
        now = utc_now() - 120
        sn = self.Sender(_timestamp=now)
        self.receive(sn.request_header)

    def test_expired_exception_reports_localtime(self):
        now = utc_now()
        ts = now - 120
        sn = self.Sender(_timestamp=ts)  # force expiry

        exc = None
        with mock.patch('mohawk.base.utc_now') as fake_now:
            fake_now.return_value = now
            try:
                self.receive(sn.request_header)
            except:
                etype, exc, tb = sys.exc_info()

        eq_(type(exc), TokenExpired)
        eq_(exc.localtime_in_seconds, now)

    def test_localtime_offset(self):
        now = utc_now() - 120
        sn = self.Sender(_timestamp=now)
        # Without an offset this will raise an expired exception.
        self.receive(sn.request_header, localtime_offset_in_seconds=-120)

    def test_localtime_skew(self):
        now = utc_now() - 120
        sn = self.Sender(_timestamp=now)
        # Without an offset this will raise an expired exception.
        self.receive(sn.request_header, timestamp_skew_in_seconds=120)

    @raises(MisComputedContentHash)
    def test_hash_tampering(self):
        sn = self.Sender()
        header = sn.request_header.replace('hash="', 'hash="nope')
        self.receive(header)

    @raises(MacMismatch)
    def test_bad_secret(self):
        cfg = {
            'id': 'my-hawk-id',
            'key': 'INCORRECT; YOU FAIL',
            'algorithm': 'sha256',
        }
        sn = self.Sender(credentials=cfg)
        self.receive(sn.request_header)

    @raises(MacMismatch)
    def test_unexpected_algorithm(self):
        cr = self.credentials.copy()
        cr['algorithm'] = 'sha512'
        sn = self.Sender(credentials=cr)

        # Validate with mismatched credentials (sha256).
        self.receive(sn.request_header)

    @raises(InvalidCredentials)
    def test_invalid_credentials(self):
        cfg = self.credentials.copy()
        # Create an invalid credentials.
        del cfg['algorithm']

        self.Sender(credentials=cfg)

    @raises(CredentialsLookupError)
    def test_unknown_id(self):
        cr = self.credentials.copy()
        cr['id'] = 'someone-else'
        sn = self.Sender(credentials=cr)

        self.receive(sn.request_header)

    @raises(MacMismatch)
    def test_bad_ext(self):
        sn = self.Sender(ext='my external data')

        header = sn.request_header.replace('my external data', 'TAMPERED')
        self.receive(header)

    def test_ext_with_quotes(self):
        sn = self.Sender(ext='quotes=""')
        self.receive(sn.request_header)
        parsed = parse_authorization_header(sn.request_header)
        eq_(parsed['ext'], 'quotes=""')

    def test_ext_with_new_line(self):
        sn = self.Sender(ext="new line \n in the middle")
        self.receive(sn.request_header)
        parsed = parse_authorization_header(sn.request_header)
        eq_(parsed['ext'], "new line \n in the middle")

    @raises(BadHeaderValue)
    def test_ext_with_illegal_chars(self):
        self.Sender(ext="something like \t is illegal")

    @raises(BadHeaderValue)
    def test_ext_with_illegal_unicode(self):
        self.Sender(ext=u'Ivan Kristi\u0107')

    @raises(BadHeaderValue)
    def test_ext_with_illegal_utf8(self):
        # This isn't allowed because the escaped byte chars are out of
        # range. It's a little odd but this is what the Node lib does
        # implicitly with its regex.
        self.Sender(ext=u'Ivan Kristi\u0107'.encode('utf8'))

    def test_app_ok(self):
        app = 'custom-app'
        sn = self.Sender(app=app)
        self.receive(sn.request_header)
        parsed = parse_authorization_header(sn.request_header)
        eq_(parsed['app'], app)

    @raises(MacMismatch)
    def test_tampered_app(self):
        app = 'custom-app'
        sn = self.Sender(app=app)
        header = sn.request_header.replace(app, 'TAMPERED-WITH')
        self.receive(header)

    def test_dlg_ok(self):
        dlg = 'custom-dlg'
        sn = self.Sender(dlg=dlg)
        self.receive(sn.request_header)
        parsed = parse_authorization_header(sn.request_header)
        eq_(parsed['dlg'], dlg)

    @raises(MacMismatch)
    def test_tampered_dlg(self):
        dlg = 'custom-dlg'
        sn = self.Sender(dlg=dlg, app='some-app')
        header = sn.request_header.replace(dlg, 'TAMPERED-WITH')
        self.receive(header)


class TestReceiver(Base):

    def setUp(self):
        super(TestReceiver, self).setUp()
        self.url = 'http://site.com/'
        self.sender = None
        self.receiver = None

    def receive(self, method='GET', **kw):
        url = kw.pop('url', self.url)
        sender = kw.pop('sender', None)
        sender_kw = kw.pop('sender_kw', {})
        sender_kw.setdefault('content', '')
        sender_kw.setdefault('content_type', '')
        sender_url = kw.pop('sender_url', url)

        credentials_map = kw.pop('credentials_map',
                                 lambda id: self.credentials)

        if sender:
            self.sender = sender
        else:
            self.sender = Sender(self.credentials, sender_url, method,
                                 **sender_kw)

        kw.setdefault('content', '')
        kw.setdefault('content_type', '')
        self.receiver = Receiver(credentials_map,
                                 self.sender.request_header, url, method,
                                 **kw)

    def respond(self, **kw):
        accept_kw = kw.pop('accept_kw', {})
        accept_kw.setdefault('content', '')
        accept_kw.setdefault('content_type', '')
        receiver = kw.pop('receiver', self.receiver)

        kw.setdefault('content', '')
        kw.setdefault('content_type', '')
        receiver.respond(**kw)
        self.sender.accept_response(receiver.response_header, **accept_kw)

        return receiver.response_header

    @raises(InvalidCredentials)
    def test_invalid_credentials_lookup(self):
        # Return invalid credentials.
        self.receive(credentials_map=lambda *a: {})

    def test_get_ok(self):
        method = 'GET'
        self.receive(method=method)
        self.respond()

    def test_post_ok(self):
        method = 'POST'
        self.receive(method=method)
        self.respond()

    @raises(MacMismatch)
    def test_respond_with_wrong_content(self):
        self.receive()
        self.respond(content='real content',
                     accept_kw=dict(content='TAMPERED WITH'))

    @raises(MacMismatch)
    def test_respond_with_wrong_content_type(self):
        self.receive()
        self.respond(content_type='text/html',
                     accept_kw=dict(content_type='application/json'))

    @raises(MacMismatch)
    def test_respond_with_wrong_url(self):
        self.receive(url='http://fakesite.com')
        wrong_receiver = self.receiver

        self.receive(url='http://realsite.com')

        self.respond(receiver=wrong_receiver)

    @raises(MacMismatch)
    def test_respond_with_wrong_method(self):
        self.receive(method='GET')
        wrong_receiver = self.receiver

        self.receive(method='POST')

        self.respond(receiver=wrong_receiver)

    @raises(MacMismatch)
    def test_respond_with_wrong_nonce(self):
        self.receive(sender_kw=dict(nonce='another-nonce'))
        wrong_receiver = self.receiver

        self.receive()

        # The nonce must match the one sent in the original request.
        self.respond(receiver=wrong_receiver)

    def test_respond_with_unhashed_content(self):
        self.receive()

        self.respond(always_hash_content=False, content=None,
                     content_type=None,
                     accept_kw=dict(accept_untrusted_content=True))

    @raises(TokenExpired)
    def test_respond_with_expired_ts(self):
        self.receive()
        hdr = self.receiver.respond(content='', content_type='')

        with mock.patch('mohawk.base.utc_now') as fn:
            fn.return_value = 0  # force an expiry
            self.sender.accept_response(hdr, content='', content_type='')

    def test_respond_with_bad_ts_skew_ok(self):
        now = utc_now() - 120

        self.receive()
        hdr = self.receiver.respond(content='', content_type='')

        with mock.patch('mohawk.base.utc_now') as fn:
            fn.return_value = now

            # Without an offset this will raise an expired exception.
            self.sender.accept_response(hdr, content='', content_type='',
                                        timestamp_skew_in_seconds=120)

    def test_respond_with_ext(self):
        self.receive()

        ext = 'custom-ext'
        self.respond(ext=ext)
        header = parse_authorization_header(self.receiver.response_header)
        eq_(header['ext'], ext)

    @raises(MacMismatch)
    def test_respond_with_wrong_app(self):
        self.receive(sender_kw=dict(app='TAMPERED-WITH', dlg='delegation'))
        self.receiver.respond(content='', content_type='')
        wrong_receiver = self.receiver

        self.receive(sender_kw=dict(app='real-app', dlg='delegation'))

        self.sender.accept_response(wrong_receiver.response_header,
                                    content='', content_type='')

    @raises(MacMismatch)
    def test_respond_with_wrong_dlg(self):
        self.receive(sender_kw=dict(app='app', dlg='TAMPERED-WITH'))
        self.receiver.respond(content='', content_type='')
        wrong_receiver = self.receiver

        self.receive(sender_kw=dict(app='app', dlg='real-dlg'))

        self.sender.accept_response(wrong_receiver.response_header,
                                    content='', content_type='')

    @raises(MacMismatch)
    def test_receive_wrong_method(self):
        self.receive(method='GET')
        wrong_sender = self.sender
        self.receive(method='POST', sender=wrong_sender)

    @raises(MacMismatch)
    def test_receive_wrong_url(self):
        self.receive(url='http://fakesite.com/')
        wrong_sender = self.sender
        self.receive(url='http://realsite.com/', sender=wrong_sender)

    @raises(MacMismatch)
    def test_receive_wrong_content(self):
        self.receive(sender_kw=dict(content='real request'),
                     content='real request')
        wrong_sender = self.sender
        self.receive(content='TAMPERED WITH', sender=wrong_sender)

    @raises(MacMismatch)
    def test_unexpected_unhashed_content(self):
        self.receive(sender_kw=dict(content=None, content_type=None,
                                    always_hash_content=False))

    @raises(ValueError)
    def test_cannot_receive_empty_content_only(self):
        content_type = 'text/plain'
        self.receive(sender_kw=dict(content='<content>',
                                    content_type=content_type),
                     content=None, content_type=content_type)

    @raises(ValueError)
    def test_cannot_receive_empty_content_type_only(self):
        content = '<content>'
        self.receive(sender_kw=dict(content=content,
                                    content_type='text/plain'),
                     content=content, content_type=None)

    @raises(MacMismatch)
    def test_receive_wrong_content_type(self):
        self.receive(sender_kw=dict(content_type='text/html'),
                     content_type='text/html')
        wrong_sender = self.sender

        self.receive(content_type='application/json',
                     sender=wrong_sender)


class TestSendAndReceive(Base):

    def test(self):
        credentials = {
            'id': 'some-id',
            'key': 'some secret',
            'algorithm': 'sha256'
        }

        url = 'https://my-site.com/'
        method = 'POST'

        # The client sends a request with a Hawk header.
        content = 'foo=bar&baz=nooz'
        content_type = 'application/x-www-form-urlencoded'

        sender = Sender(credentials,
                        url, method,
                        content=content,
                        content_type=content_type)

        # The server receives a request and authorizes access.
        receiver = Receiver(lambda id: credentials,
                            sender.request_header,
                            url, method,
                            content=content,
                            content_type=content_type)

        # The server responds with a similar Hawk header.
        content = 'we are friends'
        content_type = 'text/plain'
        receiver.respond(content=content,
                         content_type=content_type)

        # The client receives a response and authorizes access.
        sender.accept_response(receiver.response_header,
                               content=content,
                               content_type=content_type)
