#
# CLI interactions with the plotman script.
#

import datetime
import os
from flask.helpers import make_response
import psutil
import re
import signal
import shutil
import time
import traceback
import yaml

from flask import Flask, jsonify, abort, request, flash
from subprocess import Popen, TimeoutExpired, PIPE
from common.models import plottings as pl
from web import app, db, utils
from web.models.plotman import PlottingSummary
from . import worker as w
from . import chia as c

PLOTMAN_SCRIPT = '/chia-blockchain/venv/bin/plotman'

# Don't query plotman unless at least this long since last time.
RELOAD_MINIMUM_SECS = 30

def load_plotting_summary():
    plottings = db.session.query(pl.Plotting).all()
    return PlottingSummary(plottings)

def load_plotters():
    plotters = []
    for plotter in w.load_worker_summary().plotters:
        plotters.append({
            'hostname': plotter.hostname,
            'displayname': plotter.displayname,
            'plotting_status': plotter.plotting_status(),
            'archiving_status': plotter.archiving_status(),
            'archiving_enabled': plotter.archiving_enabled()
        })
    return plotters

def start_plotman(plotter):
    app.logger.info("Starting Plotman run...")
    try:
        response = utils.send_post(plotter, "/actions/", {"service": "plotting","action": "start"}, debug=False)
    except:
        app.logger.info(traceback.format_exc())
        flash('Failed to start Plotman plotting run!', 'danger')
        flash('Please see log files.', 'warning')
    else:
        if response.status_code == 200:
            flash('Plotman started successfully.', 'success')
        else:
            flash("<pre>{0}</pre>".format(response.content.decode('utf-8')), 'danger')

def action_plots(action, plot_ids):
    plots_by_worker = group_plots_by_worker(plot_ids)
    app.logger.info("About to {0} plots: {1}".format(action, plots_by_worker))
    error = False
    error_message = ""
    for hostname in plots_by_worker.keys():
        try:
            plotter = w.get_worker_by_hostname(hostname)
            plot_ids = plots_by_worker[hostname]
            response = utils.send_post(plotter, "/actions/", debug=False,
                payload={"service": "plotting","action": action, "plot_ids": plot_ids}
            )
            if response.status_code != 200:
                error_message += response.content.decode('utf-8') + "\n"
        except:
            error = True
            app.logger.info(traceback.format_exc())
    if error:
        flash('Failed to action all plots!', 'danger')
        flash('<pre>{0}</pre>'.format(error_message), 'warning')
    else:
        flash('Plotman was able to {0} the selected plots successfully.'.format(
            action), 'success')

def group_plots_by_worker(plot_ids):
    plots_by_worker = {}
    all_plottings = load_plotting_summary()
    for plot_id in plot_ids:
        hostname = None
        for plot in all_plottings.rows:
            if plot['plot_id'] == plot_id:
                hostname = plot['worker']
        if hostname:
            if not hostname in plots_by_worker:
                plots_by_worker[hostname] = []
            plots_by_worker[hostname].append(plot_id)
    return plots_by_worker

def stop_plotman(plotter):
    app.logger.info("Stopping Plotman run...")
    try:
        response = utils.send_post(plotter, "/actions/", payload={"service": "plotting","action": "stop"}, debug=False)
    except:
        app.logger.info(traceback.format_exc())
        flash('Failed to stop Plotman plotting run!', 'danger')
        flash('Please see /root/.chia/plotman/logs/plotman.log', 'warning')
    else:
        if response.status_code == 200:
            flash('Plotman stopped successfully.  No new plots will be started, but existing ones will continue on.', 'success')
        else:
            flash("<pre>{0}</pre>".format(response.content.decode('utf-8')), 'danger')

def start_archiving(plotter):
    app.logger.info("Starting Archiver....")
    try:
        response = utils.send_post(plotter, "/actions/", {"service": "archiving","action": "start"}, debug=False)
    except:
        app.logger.info(traceback.format_exc())
        flash('Failed to start Plotman archiver!', 'danger')
        flash('Please see log files.', 'warning')
    else:
        if response.status_code == 200:
            flash('Archiver started successfully.', 'success')
        else:
            flash("<pre>{0}</pre>".format(response.content.decode('utf-8')), 'danger')

def stop_archiving(plotter):
    app.logger.info("Stopping Archiver run....")
    try:
        response = utils.send_post(plotter, "/actions/", payload={"service": "archiving","action": "stop"}, debug=False)
    except:
        app.logger.info(traceback.format_exc())
        flash('Failed to stop Plotman archiver', 'danger')
        flash('Please see /root/.chia/plotman/logs/archiver.log', 'warning')
    else:
        if response.status_code == 200:
            flash('Archiver stopped successfully.', 'success')
        else:
            flash("<pre>{0}</pre>".format(response.content.decode('utf-8')), 'danger')

def load_key_pk(type):
    keys = c.load_keys_show()
    m = re.search('{0} public key .*: (\w+)'.format(type), keys.rows[0]['details'])
    if m:
        return m.group(1)
    return None

def load_pool_contract_address():
    plotnfts = c.load_plotnfts()
    if len(plotnfts.rows) == 1:
        m = re.search('Pool contract address .*: (\w+)'.format(type), plotnfts.rows[0]['details'])
        if m:
            return m.group(1)
    elif len(plotnfts.rows) > 1:
        app.logger.info("Did not find a unique Pool contract address as multiple plotnfts exist.  Not replacing in plotman.yaml.")
    return None

def load_config_replacements():
    replacements = []
    farmer_pk = load_key_pk('Farmer')
    if farmer_pk:
        #app.logger.info("FARMER_PK: {0}".format(farmer_pk))
        replacements.append([ 'farmer_pk:\s+REPLACE_WITH_THE_REAL_VALUE.*$', 'farmer_pk: '+ farmer_pk])
    pool_pk = load_key_pk('Pool')
    if pool_pk:
        #app.logger.info("POOL_PK: {0}".format(pool_pk))
        replacements.append([ 'pool_pk:\s+REPLACE_WITH_THE_REAL_VALUE.*$', 'pool_pk: '+ pool_pk])
    pool_contract_address = load_pool_contract_address()
    if pool_contract_address:
        #app.logger.info("POOL_CONTRACT_ADDRESS: {0}".format(pool_contract_address))
        replacements.append([ 'pool_contract_address:\s+REPLACE_WITH_THE_REAL_VALUE.*$', 'pool_contract_address: '+ pool_contract_address])
    return replacements

def load_config(plotter):
    replacements = []
    try:
        replacements = load_config_replacements()
    except:
        app.logger.info("Unable to load replacements on install with mode={0}".format(os.environ['mode']))
        app.logger.info(traceback.format_exc())
    lines = []
    config = utils.send_get(plotter, "/configs/plotting", debug=False).content.decode('utf-8')
    replaces = 0
    for line in config.splitlines():
        for replacement in replacements:
            (line, num_replaces) = re.subn(replacement[0], replacement[1], line)
            replaces += num_replaces
        lines.append(line)
    if replaces > 0:
        #app.logger.info("Return true for replaced.")
        return [ True, '\n'.join(lines) ]
    else:
        #app.logger.info("Return false for replaced.")
        return [ False, '\n'.join(lines) ]

def save_config(plotter, config):
    try: # Validate the YAML first
        yaml.safe_load(config)
    except Exception as ex:
        app.logger.info(traceback.format_exc())
        flash('Updated plotman.yaml failed validation! Fix and save or refresh page.', 'danger')
        flash(str(ex), 'warning')
    try:
        response = utils.send_put(plotter, "/configs/plotting", config, debug=False)
    except Exception as ex:
        flash('Failed to save config to plotter.  Please check log files.', 'danger')
        flash(str(ex), 'warning')
    else:
        if response.status_code == 200:
            flash('Nice! Plotman\'s plotman.yaml validated and saved successfully.', 'success')
        else:
            flash("<pre>{0}</pre>".format(response.content.decode('utf-8')), 'danger')

def analyze(plot_file, plotters):
    # Don't know which plotter might have the plot result so try them in-turn
    for plotter in plotters:
        if plotter.latest_ping_result != "Responding":
            app.logger.info("Skipping analyze call to {0} as last ping was: {1}".format( \
                plotter.hostname, plotter.latest_ping_result))
            continue
        try:
            app.logger.info("Trying {0} for analyze....".format(plotter.hostname))
            payload = {"service":"plotting", "action":"analyze", "plot_file": plot_file }
            response = utils.send_post(plotter, "/analysis/", payload, debug=False)
            if response.status_code == 200:
                return response.content.decode('utf-8')
            elif response.status_code == 404:
                app.logger.info("Plotter on {0} did not have plot log for {1}".format(plotter.hostname, plot_file))
            else:
                app.logger.info("Plotter on {0} returned an unexpected error: {1}".format(plotter.hostname, response.status_code))
        except:
            app.logger.info(traceback.format_exc())
    return make_response("Sorry, not plotting job log found.  Perhaps plot was made elsewhere?", 200)

def load_plotting_keys():
    farmer_pk = load_key_pk('Farmer')
    pool_pk = load_key_pk('Pool')
    pool_contract_address = load_pool_contract_address()
    if not farmer_pk:
        farmer_pk = None if os.environ['farmer_pk'] == 'null' else os.environ['farmer_pk']
    if not pool_pk:
        pool_pk = None if os.environ['pool_pk'] == 'null' else os.environ['pool_pk']
    if not pool_contract_address:
        pool_contract_address = None if os.environ['pool_contract_address'] == 'null' else os.environ['pool_contract_address']
    return [farmer_pk, pool_pk, pool_contract_address]