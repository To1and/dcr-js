
import b2dCoreCode from '../resources/bpmn2dcr.py?raw'; 

export const convertBPMNToDCRWithPyodide = async (bpmnContent: string): Promise<string> => {
  try {
    const { loadPyodide } = await import('pyodide');
    const pyodide = await loadPyodide({
      indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.27.5/full/'
    });
    
    pyodide.runPython(b2dCoreCode); 
    
    pyodide.globals.set("bpmn_content", bpmnContent);
    

    pyodide.runPython(`
import traceback


converter = Bpmn2DcrConverter()
dcr_output = ""

try:

    dcr_output = converter.translate(bpmn_content)
except Exception as e:
    dcr_output = f"PYTHON_CONVERTER_UNHANDLED_ERROR: {str(e)}\\n{traceback.format_exc()}"

dcr_result_from_python = dcr_output
`);
    

    return pyodide.globals.get('dcr_result_from_python');

  } catch (error) {
    console.error('Error in BPMN to DCR conversion (Pyodide setup or execution):', error);

    if (error instanceof Error) {
        return `PYODIDE_SETUP_ERROR: ${error.message}\n${error.stack}`;
    }
    return `PYODIDE_SETUP_UNKNOWN_ERROR: ${String(error)}`;
  }
};