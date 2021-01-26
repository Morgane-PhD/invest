"""A Flask app with HTTP endpoints used by the InVEST Workbench."""
import codecs
import collections
from datetime import datetime
import importlib
import json
import logging
from osgeo import gdal
import pprint
import textwrap

from flask import Flask
from flask import request
import natcap.invest.cli
import natcap.invest.datastack
from natcap.invest.delineateit import delineateit

logging.basicConfig(level=logging.DEBUG)
LOGGER = logging.getLogger(__name__)

app = Flask(__name__)

# Lookup names to pass to `invest run` based on python module names
_UI_META = collections.namedtuple('UIMeta', ['run_name', 'human_name'])
MODULE_MODELRUN_MAP = {
    v.pyname: _UI_META(
        run_name=k,
        human_name=v.humanname)
    for k, v in natcap.invest.cli._MODEL_UIS.items()}


def shutdown_server():
    """Shutdown the flask server."""
    func = request.environ.get('werkzeug.server.shutdown')
    if func is None:
        raise RuntimeError('Not running with the Werkzeug Server')
    func()


@app.route('/ready', methods=['GET'])
def get_is_ready():
    """Returns something simple to confirm the server is open."""
    return 'Flask ready'


@app.route('/shutdown', methods=['GET'])
def shutdown():
    """A request to this endpoint shuts down the server."""
    shutdown_server()
    return 'Flask server shutting down...'


@app.route('/models', methods=['GET'])
def get_invest_models():
    """Gets a list of available InVEST models.
    
    Returns:
        A JSON string
    """
    LOGGER.debug('get model list')
    return natcap.invest.cli.build_model_list_json()


@app.route('/getspec', methods=['POST'])
def get_invest_getspec():
    """Gets the ARGS_SPEC dict from an InVEST model.

    Body (JSON string): "carbon"

    Returns:
        A JSON string.
    """
    target_model = request.get_json()
    target_module = natcap.invest.cli._MODEL_UIS[target_model].pyname
    model_module = importlib.import_module(name=target_module)
    LOGGER.debug(model_module.__file__)
    spec = model_module.ARGS_SPEC
    return json.dumps(spec)


@app.route('/validate', methods=['POST'])
def get_invest_validate():
    """Gets the return value of an InVEST model's validate function.

    Body (JSON string):
        model_module: string (e.g. natcap.invest.carbon)
        args: JSON string of InVEST model args keys and values

    Returns:
        A JSON string.
    """
    payload = request.get_json()
    LOGGER.debug(payload)
    target_module = payload['model_module']
    args_dict = json.loads(payload['args'])
    LOGGER.debug(args_dict)
    try:
        limit_to = payload['limit_to']
    except KeyError:
        limit_to = None
    model_module = importlib.import_module(name=target_module)
    results = model_module.validate(args_dict, limit_to=limit_to)
    LOGGER.debug(results)
    return json.dumps(results)


@app.route('/colnames', methods=['POST'])
def get_vector_colnames():
    """Get a list of column names from a vector.
    This is used to fill in dropdown menu options in a couple models.

    Body (JSON string):
        vector_path (string): path to a vector file
        
    Returns:
        a JSON string.
    """
    payload = request.get_json()
    LOGGER.debug(payload)
    vector_path = payload['vector_path']
    colnames = []  
    # a lot of times the path will be empty so don't even try to open it
    if vector_path:  
        try:
            vector = gdal.OpenEx(vector_path, gdal.OF_VECTOR)
            colnames = [defn.GetName() for defn in vector.GetLayer().schema]
        except:
            LOGGER.error(f'Could not read column names from {vector_path}')
    LOGGER.debug({'colnames': colnames})
    return json.dumps({'colnames': colnames})


@app.route('/vector_has_points', methods=['POST'])
def get_vector_has_points():
    """Return boolean indicating if a vector may contain points.
    This is used by the DelineateIt UI to determine if the 'snap points' 
    option should be enabled.

    Body (JSON string):
        vector_path (string): path to a vector file

    Returns:
        a boolean.
    """
    payload = request.get_json()
    LOGGER.debug(payload)
    vector_path = payload['vector_path']
    # default True because it may have point geometries unless proven otherwise
    has_points = True
    # a lot of times the path will be empty so don't even try to open it
    if vector_path:
        try:
            has_points = delineateit._vector_may_contain_points(vector_path)
        except:
            LOGGER.error(
                f'Could not tell if vector {vector_path} contains points.')
    LOGGER.debug({'has_points': has_points})
    return json.dumps({'has_points': has_points})


@app.route('/post_datastack_file', methods=['POST'])
def post_datastack_file():
    """Extracts InVEST model args from json, logfiles, or datastacks.

    Body (JSON string): path to file

    Returns:
        A JSON string.
    """
    filepath = request.get_json()
    stack_type, stack_info = natcap.invest.datastack.get_datastack_info(
        filepath)
    run_name, human_name = MODULE_MODELRUN_MAP[stack_info.model_name]
    result_dict = {
        'type': stack_type,
        'args': stack_info.args,
        'module_name': stack_info.model_name,
        'model_run_name': run_name,
        'model_human_name': human_name,
        'invest_version': stack_info.invest_version
    }
    LOGGER.debug(result_dict)
    return json.dumps(result_dict)


@app.route('/write_parameter_set_file', methods=['POST'])
def write_parameter_set_file():
    """Writes InVEST model args keys and values to a datastack JSON file.

    Body (JSON string):
        parameterSetPath: string
        moduleName: string(e.g. natcap.invest.carbon)
        args: JSON string of InVEST model args keys and values
        relativePaths: boolean

    Returns:
        A string.
    """
    payload = request.get_json()
    filepath = payload['parameterSetPath']
    modulename = payload['moduleName']
    args = json.loads(payload['args'])
    relative_paths = payload['relativePaths']

    natcap.invest.datastack.build_parameter_set(
        args, modulename, filepath, relative=relative_paths)
    return 'parameter set saved'


# Borrowed this function from natcap.invest.model because I assume
# that module won't persist if we eventually deprecate the Qt UI.
@app.route('/save_to_python', methods=['POST'])
def save_to_python():
    """Writes a python script with a call to an InVEST model execute function.

    Body (JSON string):
        filepath: string
        modelname: string (e.g. carbon)
        pyname: string (e.g. natcap.invest.carbon)
        args_dict: JSON string of InVEST model args keys and values

    Returns:
        A string.
    """
    payload = request.get_json()
    save_filepath = payload['filepath']
    modelname = payload['modelname']
    pyname = payload['pyname']
    args_dict = json.loads(payload['args'])

    script_template = textwrap.dedent("""\
    # coding=UTF-8
    # -----------------------------------------------
    # Generated by InVEST {invest_version} on {today}
    # Model: {modelname}

    import {py_model}

    args = {model_args}

    if __name__ == '__main__':
        {py_model}.execute(args)
    """)

    with codecs.open(save_filepath, 'w', encoding='utf-8') as py_file:
        # cast_args = dict((unicode(key), value) for (key, value)
        #                  in args_dict.items())
        args = pprint.pformat(args_dict, indent=4)  # 4 spaces

        # Tweak formatting from pprint:
        # * Bump parameter inline with starting { to next line
        # * add trailing comma to last item item pair
        # * add extra space to spacing before first item
        args = args.replace('{', '{\n ')
        args = args.replace('}', ',\n}')
        py_file.write(script_template.format(
            invest_version=natcap.invest.cli.__version__,
            today=datetime.now().strftime('%c'),
            modelname=modelname,
            py_model=pyname,
            model_args=args))

    return 'python script saved'
