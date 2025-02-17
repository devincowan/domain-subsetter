#!/usr/bin/env python3

import os
import json
import time
import tornado.auth
import tornado.web
import uuid
import shutil
import subprocess
import hashlib
from urllib.parse import urljoin
from datetime import datetime
import bbox
#import transform
import urllib.parse
import xml.etree.ElementTree as ET
import environment as env

import multiprocessing

from tornado import gen 
from tornado.httpclient import AsyncHTTPClient
import tornado.auth
from tornado.log import app_log, gen_log, access_log, LogFormatter
from tornado.log import enable_pretty_logging
enable_pretty_logging()


import jobs
executor = jobs.BackgroundWorker()

import sqldata
sql = sqldata.Connect(env.sqldb)
sql.build()

#import subset


class RequestHandler(tornado.web.RequestHandler):
    errors = []

    def get_or_error(self, argname, strip=True):
        """
        This function gets a REST input argument or returns an error message if the argument is not found
        Arguments:
        argname -- the name of the argument to get
        strip -- indicates if the whitespace will be stripped from the argument
        """
        arg = self.get_argument(argname, default=None, strip=strip)
        if arg is None:
            error = 'Could not find required parameter "%s"' % argname
            self.render("index.html", header=header, args=args, error=error)
        return arg

    def get_arg_value(self, argname, isrequired, strip=True):
        arg = self.get_argument(argname, default=None, strip=strip)
        if arg is None and isrequired:
            error = 'Could not find required parameter "%s"' % argname
            self.errors.append(error)
        return arg

    def check_for_errors(self):
        response = False
        if len(self.errors) > 0:
            response = dict(message="invalid arguments", status="fail")
        return response

class MapTest(RequestHandler):
    def get(self):
        self.render("maptest.html", title="Map Test")

class IndexNew(RequestHandler, tornado.auth.OAuth2Mixin):
    def get(self):
        self.render("index_new.html", title="New Index Page")

class Index(RequestHandler, tornado.auth.OAuth2Mixin):
    def get(self):
        self.render("index.html", title="NWM v1.2.2")

    def post(self):
        ulat = self.get_argument('ulat')
        llat = self.get_argument('llat')
        ulon = self.get_argument('ulon')
        llon = self.get_argument('llon')
        hucs = self.get_argument('hucs')

        if '' in [ulat, ulon, llat, llon]:
            self.render("index.html",
                        title="CUAHSI Subsetter v0.1",
                        msg='ERROR: Missing required input')

        # build GET url for subsetting
        query = f'llat={llat}&llon={llon}&ulat={ulat}&ulon={ulon}&hucs={hucs}'
        self.redirect('nwm/v1_2_2/subset?%s' % query)


class LccBBoxFromHUC(RequestHandler):
    """
    Get BBOX in Lambert Conformal Conic for given a HUC ID.
    """

    def get(self):
        hucstring = self.get_arg_value('hucID', True)
        hucs = hucstring.split(',')
        huclevels = [len(huc) for huc in hucs]
        box = bbox.get_bbox_from_hucs(huclevels, hucs)

        response = dict(hucID=hucs,
                        hucLevel=huclevels,
                        bbox=box)

        self.write(json.dumps(response))


class Status(RequestHandler):
    @gen.coroutine
    def get(self): #, jobid=None):
#        if jobid is None:
#            http_client = AsyncHTTPClient()
#            host_url = "{protocol}://{host}".format(**vars(self.request))
#            url = host_url + '/jobs'
#            response = yield http_client.fetch(url)
#            data = json.loads(response.body)
#            self.render('admin_status.html', jobs=data) 
#        else:
        self.render('status.html')


class Job(RequestHandler):
    @gen.coroutine
    def get(self, jobid=None):
        if jobid is None:
            self.get_all_jobs()
        else:
            self.get_job_by_id(jobid)

    @gen.coroutine
    def get_all_jobs(self):
        response = []
        jobs = sql.get_jobs()
        if jobs is None:
            response = []
        else:
            for job in jobs:
                fpath = self.get_file_url(job[2])
              
                # format dates nicely
                st = job[3]
                et = job[4]
                if st is not None:
                    st = datetime.strptime(st, '%Y-%m-%d %H:%M:%S.%f') \
                                 .strftime('%m-%d-%Y %H:%M:%S')
                if et is not None:
                    et = datetime.strptime(et, '%Y-%m-%d %H:%M:%S.%f') \
                                 .strftime('%m-%d-%Y %H:%M:%S')

                # build response object
                response.append(dict(id=job[0],
                                     status=job[1],
                                     file=fpath,
                                     start=st,
                                     end=et))
        # return serialized data
        self.write(json.dumps(response))
        self.finish()

    @gen.coroutine
    def get_job_by_id(self, jobid):
        response = None

        # todo: remove this loop and replace with a sqldata.get_job_by_guid.
        jobs = sql.get_jobs()
        for job in jobs:
            if jobid == job[0]:
                fpath = self.get_file_url(job[2])

                # format dates nicely
                st = job[3]
                et = job[4]
                if st is not None:
                    st = datetime.strptime(st, '%Y-%m-%d %H:%M:%S.%f') \
                                 .strftime('%m-%d-%Y %H:%M:%S')
                if et is not None:
                    et = datetime.strptime(et, '%Y-%m-%d %H:%M:%S.%f') \
                                 .strftime('%m-%d-%Y %H:%M:%S')

                # build response object
                response = dict(id=job[0],
                                status=job[1],
                                file=fpath,
                                start=st,
                                end=et)
                continue
        if response is None:
            response = dict(message='Job Not Found',
                            status='error')

        self.write(json.dumps(response))
        self.finish()

    def get_file_url(self, relative_file_path):
        if relative_file_path.strip() != '':
            host_url = "{protocol}://{host}".format(**vars(self.request))
            return host_url + relative_file_path
        return None

class Results(RequestHandler):
    @gen.coroutine
    def get(self):
        model = self.get_argument('model', None)
        jobid = self.get_argument('jobid', None)
        version = self.get_argument('version', None)
        if (model is None) or (jobid is None) or (version is None):
            self.write('Operation aborted: Missing arguments. Must provide "model", "version", and "jobid"')
        elif not os.path.exists(os.path.join(env.output_dir, jobid)):
            self.write(f'Could not find job id: {jobid}')
            self.finish()

        # render results page based on model and version
        template = None
        version = float(version)
        if model.lower() == 'parflow':
            if version == 1.0:
                template = 'results_pf1.html'
        elif model.lower() == 'nwm':
            if version == 1.2:
                template = 'results_nwm1_2.html'
            if version == 2.0:
                # todo: update this template for nwm 2.0
                template = 'results_nwm1_2.html'

        # render an error if template is not found.
        if template is None:
            self.write('Operation aborted: Invalid arguments provided')
            self.finish()

        # render the template
        self.render(template,
                    jobid=jobid,
                    title='Results')


#class Results(RequestHandler):
#    @gen.coroutine
#    def get(self, jobid):
#        fname = f'{jobid}.tar.gz'
#        host_url = "{protocol}://{host}".format(**vars(self.request))
#        file_url = f'{host_url}/data/{fname}'
#        self.render('results.html',
#                    jobid=jobid,
#                    title='Results')
#

class GetZip(RequestHandler):
    @gen.coroutine
    def get(self, uid):
        host_url = "{protocol}://{host}".format(**vars(self.request))
        path = os.path.join(env.output_dir, uid)
        shutil.make_archive(path, 'zip', path)
        self.redirect(f'{host_url}/data/{path}.zip')


class GetGzip(RequestHandler):
    @gen.coroutine
    def get(self, uid):
        host_url = "{protocol}://{host}".format(**vars(self.request))
        path = os.path.join(env.output_dir, uid)
        shutil.make_archive(path, 'gztar', path)
        self.redirect(f'{host_url}/data/{path}.tar.gz')


class About(RequestHandler):
    @gen.coroutine
    def get(self):
        self.render('about.html', title='About')


class Help(RequestHandler):
    @gen.coroutine
    def get(self):
        self.render('help.html', title='Help')


class Api(RequestHandler):
    @gen.coroutine
    def get(self):
        self.render('api.html', title='API Documentation')


class GettingStarted(RequestHandler):
    @gen.coroutine
    def get(self):
        self.render('getting-started.html', title='Getting Started')
