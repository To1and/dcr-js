import b2dCoreCode from '../resources/bpmn2dcr.py?raw'; 

export const convertBPMNToDCRWithPyodide = async (bpmnContent: string): Promise<string> => {
  try {
    const { loadPyodide } = await import('pyodide');
    const pyodide = await loadPyodide({
      indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.27.5/full/'
    });
    

    pyodide.runPython(b2dCoreCode);

    pyodide.globals.set("bpmn_input_content_from_js", bpmnContent);

    const pyodide_temp_bpmn_path = "/pyodide_temp_input.bpmn";
    const pyodide_temp_dcr_path = "/pyodide_temp_output.xml";


    const pythonExecutionSnippet = `
import traceback

converter_instance = Bpmn2DcrConverter(
    temp_bpmn_file_path="${pyodide_temp_bpmn_path}",
    temp_dcr_file_path="${pyodide_temp_dcr_path}"
)

dcr_output_result_str = "" 

try:

    dcr_output_result_str = converter_instance.translate(bpmn_input_content_from_js)
except Exception as e:
    dcr_output_result_str = f"PYTHON_CONVERTER_UNHANDLED_ERROR: {str(e)}\\n{traceback.format_exc()}"
`;
    
    pyodide.runPython(pythonExecutionSnippet);
                                                      
    const result = pyodide.globals.get('dcr_output_result_str');

    if (typeof result !== 'string') {
        console.error("Python did not return a string. Got:", result);
        return `PYODIDE_PYTHON_RETURN_UNEXPECTED_TYPE: Expected string from Python but got ${typeof result}. Value: ${String(result)}`;
    }
    return result;

  } catch (error) {
    console.error('Error in BPMN to DCR conversion (Pyodide setup or execution):', error);
    let errorMessage = `PYODIDE_SETUP_UNKNOWN_ERROR: ${String(error)}`;
    if (error instanceof Error) {
        const pyError = error as any; 
        if (pyError.name === 'PythonError' && pyError.message && pyError.stack) {
             errorMessage = `PYTHON_ERROR_FROM_PYODIDE: ${pyError.message}\nJS_STACK:\n${pyError.stack}`;
        } else {
            errorMessage = `PYODIDE_SETUP_ERROR: ${error.message}\n${error.stack}`;
        }
    }
    return errorMessage;
  }
};