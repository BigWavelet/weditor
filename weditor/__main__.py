#! /usr/bin/env python
#-*- encoding: utf-8 -*-

from __future__ import absolute_import
from __future__ import print_function

import os
import hashlib
import argparse
import signal
import base64
import webbrowser
from io import BytesIO

import atx
import tornado.ioloop
import tornado.web
import tornado.websocket
import tornado.escape
from tornado.escape import json_encode

from weditor import uidumplib


__dir__ = os.path.dirname(os.path.abspath(__file__))
__devices = {}

def get_device(serial):
    d = __devices.get(serial)
    if d:
        return d
    __devices[serial] = atx.connect(None if serial == 'default' else serial)
    return __devices.get(serial)


def read_file_content(filename, default=''):
    if not os.path.isfile(filename):
        return default
    with open(filename, 'rb') as f:
        return f.read()


def write_file_content(filename, content):
    with open(filename, 'w') as f:
        f.write(content.encode('utf-8'))


def sha_file(path):
    sha = hashlib.sha1()
    with open(path, 'rb') as f:
        while True:
            data = f.read(65536)
            if not data:
                break
            sha.update(data)
    return sha.hexdigest()


def virt2real(path):
    return os.path.join(os.getcwd(), path.lstrip('/'))

def real2virt(path):
    return os.path.relpath(path, os.getcwd()).replace('\\', '/')


class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        self.set_header("Access-Control-Allow-Origin", "*")
        self.set_header("Access-Control-Allow-Headers", "x-requested-with")
        self.set_header('Access-Control-Allow-Methods', 'POST, GET, PUT, DELETE, OPTIONS')

    def options(self, *args):
        self.set_status(204) # no body
        self.finish()


class VersionHandler(BaseHandler):
    def get(self):
        self.write({
            'name': '0.0.1',
        })


class DeviceScreenshotHandler(BaseHandler):
    def get(self, serial):
        print("SN", serial)
        d = get_device(serial)
        buffer = BytesIO()
        d.screenshot().save(buffer, format='JPEG')
        b64data = base64.b64encode(buffer.getvalue())
        # with open('bg.jpg', 'rb') as f:

        # b64data = base64.b64encode(f.read())
        self.write({
            "type": "jpeg",
            "encoding": "base64",
            "data": b64data,
        })


class FileHandler(BaseHandler):
    def get_file(self, path):
        _real = virt2real(path)
        self.write({
            'type': 'file',
            'size': os.path.getsize(_real),
            'name': os.path.basename(path),
            'path': path,
            'content': read_file_content(_real),
            'sha': sha_file(_real),
        })

    def get_dir(self, path):
        _real = virt2real(path)
        files = os.listdir(_real) # TODO
        rets = []
        for name in files:
            _path = os.path.join(_real, name)
            if os.path.isfile(name):
                rets.append({
                    'type': 'file',
                    'name': name,
                    'path': os.path.join(path, name),
                    'size': os.path.getsize(_path),
                    'sha': sha_file(_path),
                })
            else:
                rets.append({
                    'type': 'dir',
                    'size': 0,
                    'name': name,
                    'path': _path,
                })
        self.set_header('Content-Type', 'application/json; charset=UTF-8')
        self.write(json_encode(rets))

    def get(self, path):
        _real = virt2real(path)
        if os.path.isfile(_real):
            self.get_file(path)
        elif os.path.isdir(_real):
            self.get_dir(path)
        else:
            self.set_status(404)
            self.write({
                'description': 'file not exists'
            })

    def put(self, path):
        data = tornado.escape.json_decode(self.request.body)
        content = data.get('content')
        _real = virt2real(path)
        _dir = os.path.dirname(_real)
        if not os.path.isdir(_dir):
            os.makedirs(_dir)
        if os.path.isfile(_real):
            sha = sha_file(_real)
            if sha != data.get('sha'):
                self.set_status(422, 'Unprocessable Entity')
                self.write({
                    'description': 'file sha not match',
                })
                return
            write_file_content(_real, content)
            self.set_status(200)
        else:
            write_file_content(_real, content)
            self.set_status(201)
        self.write({
            'content': {
                'type': 'file',
                'name': os.path.basename(path),
                'path': path,
                'sha': sha_file(_real),
                'size': len(content),
            }
        })  

    def post(self, path):
        pass

    def delete(self, path):
        _real = virt2real(path)
        data = tornado.escape.json_decode(self.request.body)
        if not os.path.isfile(_real):
            self.set_status(404)
            self.write({
                'description': 'file not exists'
            })
            return
        # check sha
        sha = sha_file(_real)
        if not data or data.get('sha') != sha:
            self.set_status(422, 'Unprocessable Entity')
            self.write({
                'description': 'file sha not match'
            })
            return
        # delete file
        try:
            os.remove(_real)
            self.write({
                'content': None,
                'description': 'successfully deleted file',
            })
        except (IOError, WindowsError) as e:
            self.set_status(500)
            self.write({
                'description': 'file deleted error: {}'.format(e),
            })


class MainHandler(BaseHandler):
    def get(self):
        self.write("Hello")
        # self.render('index.html')

    def post(self):
        self.write("Good")


class DeviceUIViewHandler(BaseHandler):
    def get(self, serial):
        d = get_device(serial)
        self.write({
            'nodes': uidumplib.get_uiview(d)
        })


def make_app(settings={}):
    # REST API REFERENCE
    # https://developer.github.com/v3/repos/contents/
    application = tornado.web.Application([
        (r"/", MainHandler),
        (r"/api/v1/version", VersionHandler),
        (r"/api/v1/contents/([^/]*)", FileHandler),
        (r"/api/v1/devices/([^/]+)/screenshot", DeviceScreenshotHandler),
        (r"/api/v1/devices/([^/]+)/uiview", DeviceUIViewHandler),
    ], **settings)
    return application

is_closing = False

def signal_handler(signum, frame):
    global is_closing
    print('exiting...')
    is_closing = True

def try_exit(): 
    global is_closing
    if is_closing:
        # clean up here
        tornado.ioloop.IOLoop.instance().stop()
        print('exit success')


def run_web(debug=False):
    application = make_app({
        'static_path': os.path.join(__dir__, 'static'),
        'template_path': os.path.join(__dir__, 'static'),
        'debug': debug,
    })
    port = 17310
    print('listen port', port)
    signal.signal(signal.SIGINT, signal_handler)
    application.listen(port)
    tornado.ioloop.PeriodicCallback(try_exit, 100).start() 
    tornado.ioloop.IOLoop.instance().start()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('-q', '--quiet', action='store_true', help='quite mode, no open new browser')
    ap.add_argument('port', nargs='?', default=17310, help='local listen port for weditor')

    args = ap.parse_args()
    open_browser = not args.quiet

    if open_browser:
        # webbrowser.open(url, new=2)
        webbrowser.open('http://atx.open.netease.com', new=2)
    run_web()


if __name__ == '__main__':
    main()
