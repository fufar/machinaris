
import pathlib
import pytz
import os
import random
import requests
import time
import traceback

from datetime import datetime
from flask import Flask, flash, redirect, render_template, abort, escape, \
        request, session, url_for, send_from_directory, make_response
from flask_babel import _, lazy_gettext as _l

from common.config import globals
from common.utils import fiat
from common.models import pools as po
from web import app, utils
from web.actions import chia, pools as p, plotman, chiadog, worker, \
        log_handler, stats, warnings, forktools, mapping

def get_lang(request):
    try:
        accept = request.headers['Accept-Language']
        match = request.accept_languages.best_match(app.config['LANGUAGES'])
        # Workaround for dumb babel match method suggesting 'en' for 'nl' instead of 'nl_NL'
        if match == 'en' and not accept.startswith('en'):
            first_accept = accept.split(',')[0]  # Like 'nl'
            alternative = "{0}_{1}".format(first_accept, first_accept.upper())
            if alternative in app.config['LANGUAGES']:
                return alternative
        app.logger.info("ROUTES: Accept-Language: {0}  ---->  matched locale: {1}".format(accept, match))
        return request.accept_languages.best_match(app.config['LANGUAGES'])
    except:
        app.logger.info("ROUTES: Request had no Accept-Language, returning default locale of 'en'")
        return "en" 

def find_selected_worker(hosts, hostname, blockchain= None):
    if len(hosts) == 0:
        return None
    if not blockchain:
        hosts[0].workers[0]
    for host in hosts:
        for worker in host.workers:
            if worker['hostname'] == hostname and worker['blockchain'] == blockchain:
                return worker
    return hosts[0].workers[0]

@app.route('/')
def landing():
    gc = globals.load()
    if not globals.is_setup():
        return redirect(url_for('setup'))
    for accept in request.accept_languages.values():
        app.logger.info("ACCEPT IS {0}".format(accept))
    app.logger.info("LANGUAGES IS {0}".format(app.config['LANGUAGES']))
    lang = get_lang(request)
    msg = random.choice(list(open('web/static/landings/{0}.txt'.format(lang))))
    if msg.endswith(".png"):
        msg = "<img style='height: 150px' src='{0}' />".format(url_for('static', filename='/landings/' + msg))
    return render_template('landing.html', random_message=msg)

@app.route('/index')
def index():
    gc = globals.load()
    if not globals.is_setup():
        return redirect(url_for('setup'))
    if not utils.is_controller():
        return redirect(url_for('controller'))
    workers = worker.load_worker_summary()
    farm_summary = chia.load_farm_summary()
    plotting = plotman.load_plotting_summary_by_blockchains(farm_summary.farms.keys())
    selected_blockchain = farm_summary.selected_blockchain()
    chia.challenges_chart_data(farm_summary)
    p.partials_chart_data(farm_summary)
    stats.load_daily_diff(farm_summary)
    warnings.check_warnings(request.args)
    return render_template('index.html', reload_seconds=120, farms=farm_summary.farms, \
        plotting=plotting, workers=workers, global_config=gc, selected_blockchain=selected_blockchain)

@app.route('/summary', methods=['GET', 'POST'])
def summary():
    gc = globals.load()
    if request.method == 'POST':
        fiat.save_local_currency(request.form.get('local_currency'))
        flash(_("Saved local currency setting."), 'success')
    summaries = chia.load_summaries()
    return render_template('summary.html', reload_seconds=120, summaries=summaries, global_config=gc,
        exchange_rates=fiat.load_exchange_rates_cache(), local_currency=fiat.get_local_currency(), 
        local_cur_sym=fiat.get_local_currency_symbol(), lang=get_lang(request))

@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if globals.is_setup():
        return redirect(url_for('index'))
    key_paths = globals.get_key_paths()
    app.logger.debug("Setup found these key paths: {0}".format(key_paths))
    show_setup = True
    if request.method == 'POST':
        if request.form.get('action') == 'generate':
            show_setup = not chia.generate_key(key_paths[0], globals.enabled_blockchains()[0])
        elif request.form.get('action') == 'import':
            show_setup = not chia.import_key(key_paths[0], request.form.get('mnemonic'), globals.enabled_blockchains()[0])
    [download_percentage, blockchain_download_size] = globals.blockchain_downloading()
    app.logger.info(_("Blockchain download") + f" @ {download_percentage}% - {blockchain_download_size}")
    if show_setup:
        return render_template('setup.html', key_paths = key_paths, 
            blockchain_download_size=blockchain_download_size, download_percentage=download_percentage)
    else:
        return redirect(url_for('index'))

@app.route('/controller')
def controller():
    return render_template('controller.html', controller_url = utils.get_controller_web())

@app.route('/plotting/jobs', methods=['GET', 'POST'])
def plotting_jobs():
    gc = globals.load()
    if request.method == 'POST':
        if request.form.get('action') == 'start':
            hostname= request.form.get('hostname')
            blockchain = request.form.get('blockchain')
            plotter = worker.get_worker(hostname, blockchain)
            if request.form.get('service') == 'plotting':
                plotman.start_plotman(plotter)
        elif request.form.get('action') == 'stop':
            hostname= request.form.get('hostname')
            blockchain = request.form.get('blockchain')
            plotter = worker.get_worker(hostname, blockchain)
            if request.form.get('service') == 'plotting':
                plotman.stop_plotman(plotter)
        elif request.form.get('action') in ['suspend', 'resume', 'kill']:
            action = request.form.get('action')
            plot_ids = request.form.getlist('plot_id')
            plotman.action_plots(action, plot_ids)
        else:
            app.logger.info(_("Unknown plotting form") + ": {0}".format(request.form))
        return redirect(url_for('plotting_jobs')) # Force a redirect to allow time to update status
    plotters = plotman.load_plotters()
    plotting = plotman.load_plotting_summary()
    job_stats = stats.load_plotting_stats()
    return render_template('plotting/jobs.html', reload_seconds=120,  plotting=plotting, 
        plotters=plotters, job_stats=job_stats, global_config=gc, lang=get_lang(request))

@app.route('/plotting/workers', methods=['GET', 'POST'])
def plotting_workers():
    gc = globals.load()
    if request.method == 'POST':
        if request.form.get('action') == 'start':
            hostname= request.form.get('hostname')
            blockchain = request.form.get('blockchain')
            plotter = worker.get_worker(hostname, blockchain)
            if request.form.get('service') == 'archiving':
                plotman.start_archiving(plotter)
        elif request.form.get('action') == 'stop':
            hostname= request.form.get('hostname')
            blockchain = request.form.get('blockchain')
            plotter = worker.get_worker(hostname, blockchain)
            if request.form.get('service') == 'archiving':
                plotman.stop_archiving(plotter)
        return redirect(url_for('plotting_workers')) # Force a redirect to allow time to update status
    plotters = plotman.load_plotters()
    disk_usage = stats.load_recent_disk_usage('plotting')
    return render_template('plotting/workers.html', plotters=plotters, disk_usage=disk_usage, global_config=gc)

@app.route('/farming/plots')
def farming_plots():
    if request.args.get('analyze'):  # Xhr with a plot_id
        plot_id = request.args.get('analyze')
        return plotman.analyze(plot_id)
    elif request.args.get('check'):  # Xhr with a plot_id
        plot_id = request.args.get('check')
        return chia.check(plot_id)
    gc = globals.load()
    farmers = chia.load_farmers()
    plots = chia.load_plots_farming()
    return render_template('farming/plots.html', farmers=farmers, plots=plots, global_config=gc, 
        lang=get_lang(request))

@app.route('/farming/data')
def farming_data():
    try:
        [draw, recordsTotal, recordsFiltered, data] = chia.load_plots(request.args)
        return make_response({'draw': draw, 'recordsTotal': recordsTotal, 'recordsFiltered': recordsFiltered, "data": data}, 200)
    except: 
        traceback.print_exc()
    return make_response(_("Error! Please see logs."), 500)

@app.route('/farming/workers')
def farming_workers():
    gc = globals.load()
    farmers = chia.load_farmers()
    daily_summaries = stats.load_daily_farming_summaries()
    disk_usage = stats.load_current_disk_usage('plots')
    return render_template('farming/workers.html', farmers=farmers, 
        daily_summaries=daily_summaries, disk_usage=disk_usage, global_config=gc)

@app.route('/alerts', methods=['GET', 'POST'])
def alerts():
    gc = globals.load()
    if request.method == 'POST':
        if request.form.get('action') == 'start':
            w = worker.get_worker(request.form.get('hostname'))
            chiadog.start_chiadog(w)
        elif request.form.get('action') == 'stop':
            w = worker.get_worker(request.form.get('hostname'))
            chiadog.stop_chiadog(w)
        elif request.form.get('action') == 'remove':
            chiadog.remove_alerts(request.form.getlist('unique_id'))
        elif request.form.get('action') == 'purge':
            chiadog.remove_all_alerts()
        else:
            app.logger.info(_("Unknown alerts form") + ": {0}".format(request.form))
        return redirect(url_for('alerts')) # Force a redirect to allow time to update status
    farmers = chiadog.load_farmers()
    notifications = chiadog.get_notifications()
    return render_template('alerts.html', reload_seconds=120, farmers=farmers,
        notifications=notifications, global_config=gc, lang=get_lang(request))

@app.route('/wallet', methods=['GET', 'POST'])    
def wallet():
    gc = globals.load()
    selected_blockchain = worker.default_blockchain()
    if request.method == 'POST':
        if request.form.get('local_currency'):
            app.logger.info("Saving local currency setting of: {0}".format(request.form.get('local_currency')))
            fiat.save_local_currency(request.form.get('local_currency'))
            flash(_("Saved local currency setting."), 'success')
        else:
            app.logger.info("Saving {0} cold wallet address of: {1}".format(request.form.get('blockchain'), request.form.get('cold_wallet_address')))
            selected_blockchain = request.form.get('blockchain')
            chia.save_cold_wallet_addresses(request.form.get('blockchain'), request.form.get('cold_wallet_address'))
            flash(_("Saved cold wallet addresses."), 'success')
    wallets = chia.load_wallets()
    return render_template('wallet.html', wallets=wallets, global_config=gc, selected_blockchain = selected_blockchain, 
        reload_seconds=120, exchange_rates=fiat.load_exchange_rates_cache(), local_currency=fiat.get_local_currency(), 
        local_cur_sym=fiat.get_local_currency_symbol(), lang=get_lang(request))

@app.route('/keys')
def keys():
    gc = globals.load()
    selected_blockchain = worker.default_blockchain()
    keys = chia.load_keys()
    key_paths = globals.get_key_paths()
    return render_template('keys.html', keys=keys, selected_blockchain = selected_blockchain,
        key_paths=key_paths, global_config=gc)

@app.route('/workers', methods=['GET', 'POST'])
def workers():
    gc = globals.load()
    if request.method == 'POST':
        if request.form.get('action') == "prune":
            worker.prune_workers_status(request.form.getlist('worker'))
    wkrs = worker.load_worker_summary()
    return render_template('workers.html', reload_seconds=120, 
        workers=wkrs, global_config=gc, lang=get_lang(request))

@app.route('/worker', methods=['GET'])
def worker_route():
    gc = globals.load()
    hostname=request.args.get('hostname')
    blockchain=request.args.get('blockchain')
    wkr = worker.get_worker(hostname, blockchain)
    plotting = plotman.load_plotting_summary(hostname=hostname)
    plots_disk_usage = stats.load_current_disk_usage('plots',hostname=hostname)
    plotting_disk_usage = stats.load_current_disk_usage('plotting',hostname=hostname)
    warnings = worker.generate_warnings(wkr)
    return render_template('worker.html', worker=wkr, 
        plotting=plotting, plots_disk_usage=plots_disk_usage, 
        plotting_disk_usage=plotting_disk_usage, warnings=warnings, global_config=gc,
        lang=get_lang(request))

@app.route('/blockchains')
def blockchains():
    gc = globals.load()
    selected_blockchain = worker.default_blockchain()
    blockchains = chia.load_blockchains()
    return render_template('blockchains.html', reload_seconds=120, selected_blockchain = selected_blockchain, 
        blockchains=blockchains, global_config=gc, lang=get_lang(request))

@app.route('/connections', methods=['GET', 'POST'])
def connections():
    gc = globals.load()
    selected_blockchain = worker.default_blockchain()
    if request.method == 'POST':
        if request.form.get('maxmind_account'):
            mapping.save_settings(request.form.get('maxmind_account'), request.form.get('maxmind_license_key'), request.form.get('mapbox_access_token'))
            flash(_("Saved mapping settings.  Please allow 10 minutes to generate location information for the map."), 'success')
        else:
            selected_blockchain = request.form.get('blockchain')
            if request.form.get('action') == "add":
                chia.add_connection(request.form.get("connection"), request.form.get('hostname'), request.form.get('blockchain'))
            elif request.form.get('action') == 'remove':
                chia.remove_connection(request.form.getlist('nodeid'), request.form.get('hostname'), request.form.get('blockchain'))
            else:
                app.logger.info(_("Unknown form action") + ": {0}".format(request.form))
    connections = chia.load_connections(lang=get_lang(request))
    return render_template('connections.html', reload_seconds=120, selected_blockchain = selected_blockchain,
        maxmind_license = mapping.load_maxmind_license(), mapbox_license = mapping.load_mapbox_license(), marker_hues=mapping.generate_marker_hues(connections),
        connections=connections, global_config=gc, lang=get_lang(request))

@app.route('/settings/plotting', methods=['GET', 'POST'])
def settings_plotting():
    selected_worker_hostname = None
    blockchains = globals.enabled_blockchains()
    selected_blockchain = None
    gc = globals.load()
    if request.method == 'POST':
        selected_worker_hostname = request.form.get('worker')
        selected_blockchain = request.form.get('blockchain')
        plotman.save_config(worker.get_worker(selected_worker_hostname, selected_blockchain), selected_blockchain, request.form.get("config"))
    workers_summary = worker.load_worker_summary()
    selected_worker = find_selected_worker(workers_summary.plotters(), selected_worker_hostname, selected_blockchain)
    if not selected_blockchain:
        selected_blockchain = selected_worker['blockchain']
    app.logger.info(selected_worker['hostname'])
    return render_template('settings/plotting.html', blockchains=blockchains, selected_blockchain=selected_blockchain,
        workers=workers_summary.plotters, selected_worker=selected_worker['hostname'], global_config=gc)

@app.route('/settings/farming', methods=['GET', 'POST'])
def settings_farming():
    selected_worker_hostname = None
    blockchains = globals.enabled_blockchains()
    selected_blockchain = None
    gc = globals.load()
    if request.method == 'POST':
        selected_worker_hostname = request.form.get('worker')
        selected_blockchain = request.form.get('blockchain')
        chia.save_config(worker.get_worker(selected_worker_hostname, selected_blockchain), selected_blockchain, request.form.get("config"))
    workers_summary = worker.load_worker_summary()
    selected_worker = find_selected_worker(workers_summary.farmers_harvesters(), selected_worker_hostname, selected_blockchain)
    if not selected_blockchain:
        selected_blockchain = selected_worker['blockchain']
    return render_template('settings/farming.html', blockchains=blockchains, selected_blockchain=selected_blockchain,
        workers=workers_summary.farmers_harvesters, selected_worker=selected_worker['hostname'], global_config=gc)

@app.route('/settings/alerts', methods=['GET', 'POST'])
def settings_alerts():
    selected_worker_hostname = None
    blockchains = globals.enabled_blockchains()
    selected_blockchain = None
    gc = globals.load()
    if request.method == 'POST':
        selected_worker_hostname = request.form.get('worker')
        selected_blockchain = request.form.get('blockchain')
        chiadog.save_config(worker.get_worker(selected_worker_hostname, selected_blockchain), selected_blockchain, request.form.get("config"))
    farmers = chiadog.load_farmers()
    selected_worker = find_selected_worker(farmers, selected_worker_hostname, selected_blockchain)
    if not selected_blockchain:
        selected_blockchain = selected_worker['blockchain']
    return render_template('settings/alerts.html', blockchains=blockchains, selected_blockchain=selected_blockchain,
        workers=farmers, selected_worker=selected_worker['hostname'], global_config=gc)

@app.route('/settings/pools', methods=['GET', 'POST'])
def settings_pools():
    gc = globals.load()
    selected_blockchain = worker.default_blockchain()
    if request.method == 'POST':
        selected_blockchain = request.form.get('blockchain')
        selected_fullnode = worker.get_fullnode(selected_blockchain)
        launcher_ids = request.form.getlist('{0}-launcher_id'.format(selected_blockchain))
        wallet_nums = request.form.getlist('{0}-wallet_num'.format(selected_blockchain))
        choices = []
        for num in wallet_nums:
            choices.append(request.form.get('{0}-choice-{1}'.format(selected_blockchain, num)))
        pool_urls = request.form.getlist('{0}-pool_url'.format(selected_blockchain))
        current_pool_urls = request.form.getlist('{0}-current_pool_url'.format(selected_blockchain))
        p.send_request(selected_fullnode, selected_blockchain, launcher_ids, choices, pool_urls, wallet_nums, current_pool_urls)
    pool_configs = p.get_pool_configs()
    fullnodes_by_blockchain = worker.get_fullnodes_by_blockchain()
    poolable_blockchains = []
    for pb in po.POOLABLE_BLOCKCHAINS:
        if pb in fullnodes_by_blockchain:
            poolable_blockchains.append(pb)
    return render_template('settings/pools.html',  global_config=gc, fullnodes_by_blockchain=fullnodes_by_blockchain,
        pool_configs=pool_configs, blockchains=poolable_blockchains, selected_blockchain=selected_blockchain)

@app.route('/settings/tools', methods=['GET', 'POST'])
def settings_tools():
    selected_worker_hostname = None
    blockchains = globals.enabled_blockchains()
    selected_blockchain = None
    gc = globals.load()
    if request.method == 'POST':
        selected_worker_hostname = request.form.get('worker')
        selected_blockchain = request.form.get('blockchain')
        forktools.save_config(worker.get_worker(selected_worker_hostname, selected_blockchain), selected_blockchain, request.form.get("config"))
    farmers = chiadog.load_farmers()
    selected_worker = find_selected_worker(farmers, selected_worker_hostname, selected_blockchain)
    if not selected_blockchain:
        selected_blockchain = selected_worker['blockchain']
    return render_template('settings/tools.html', blockchains=blockchains, selected_blockchain=selected_blockchain,
        workers=farmers, selected_worker=selected_worker['hostname'], global_config=gc)

@app.route('/settings/config', defaults={'path': ''})
@app.route('/settings/config/<path:path>')
def views_settings_config(path):
    config_type = request.args.get('type')
    w = worker.get_worker(request.args.get('worker'), request.args.get('blockchain'))
    if not w:
        app.logger.info(_l("No worker at %(worker)s for blockchain %(blockchain)s. Please select another blockchain.",
            worker=request.args.get('worker'), blockchain=request.args.get('blockchain')))
        abort(404)
    if config_type == "alerts":
        try:
            response = make_response(chiadog.load_config(w, request.args.get('blockchain')), 200)
        except requests.exceptions.ConnectionError as ex:
            response = make_response(_("For Alerts config, found no responding fullnode found for %(blockchain)s. Please check your workers.", blockchain=escape(request.args.get('blockchain'))))
    elif config_type == "farming":
        try:
            response = make_response(chia.load_config(w, request.args.get('blockchain')), 200)
        except requests.exceptions.ConnectionError as ex:
            response = make_response(_("For Farming config, found no responding fullnode found for %(blockchain)s. Please check your workers.", blockchain=escape(request.args.get('blockchain'))))
    elif config_type == "plotting":
        try:
            [replaced, config] = plotman.load_config(w, request.args.get('blockchain'))
            response = make_response(config, 200)
            response.headers.set('ConfigReplacementsOccurred', replaced)
        except requests.exceptions.ConnectionError as ex:
            response = make_response(_("For Plotting config, found no responding fullnode found for %(blockchain)s. Please check your workers.", blockchain=escape(request.args.get('blockchain'))))
    elif config_type == "tools":
        try:
            response = make_response(forktools.load_config(w, request.args.get('blockchain')), 200)
        except requests.exceptions.ConnectionError as ex:
            response = make_response(_("No responding fullnode found for %(blockchain)s. Please check your workers.", blockchain=escape(request.args.get('blockchain'))))
    else:
        abort("Unsupported config type: {0}".format(config_type), 400)
    response.mimetype = "application/x-yaml"
    return response

@app.route('/logs')
def logs():
    return render_template('logs.html') 

@app.route('/logfile')
def logfile():
    w = worker.get_worker(request.args.get('hostname'), request.args.get('blockchain'))
    log_type = request.args.get("log")
    if log_type in [ 'alerts', 'farming', 'plotting', 'archiving', 'apisrv', 'webui', 'pooling']:
        log_id = request.args.get("log_id")
        blockchain = request.args.get("blockchain")
        return log_handler.get_log_lines(get_lang(request), w, log_type, log_id, blockchain)
    else:
        abort(500, _("Unsupported log type") + ": {0}".format(log_type))

@app.route('/worker_launch')
def worker_launch():
    [farmer_pk, pool_pk, pool_contract_address] = plotman.load_plotting_keys('chia')
    pathlib.Path('/root/.chia/machinaris/tmp/').mkdir(parents=True, exist_ok=True)
    pathlib.Path('/root/.chia/machinaris/tmp/worker_launch.tmp').touch()
    return render_template('worker_launch.html', farmer_pk=farmer_pk, 
        pool_pk=pool_pk, pool_contract_address=pool_contract_address)

@app.route('/pools')
def pools():
    gc = globals.load()
    selected_blockchain = worker.default_blockchain()
    return render_template('pools.html', pools= p.load_pools(), global_config=gc, selected_blockchain = selected_blockchain)

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
            'favicon.ico', mimetype='image/vnd.microsoft.icon')
