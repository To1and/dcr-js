import b2dCoreCode from '../resources/bpmn2dcr_core.py?raw';

export const convertBPMNToDCRWithPyodide = async (bpmnContent: string): Promise<string> => {
  try {
    const { loadPyodide } = await import('pyodide');
    const pyodide = await loadPyodide({
      indexURL: 'https://cdn.jsdelivr.net/pyodide/v0.27.5/full/'
    });
    
    pyodide.runPython(b2dCoreCode);
    pyodide.globals.set("bpmn_content", bpmnContent);
    
    pyodide.runPython(`

input_file = "/tmp/input.bpmn"
output_file = "/tmp/output.xml"


with open(input_file, "w") as f:
    f.write(bpmn_content)


translator = Translator()
translator.translate_bpmn_to_dcr(input_file, output_file)


with open(output_file, "r") as f:
    dcr_content = f.read()
`);
    
    return pyodide.globals.get('dcr_content');
  } catch (error) {
    console.error('Error in BPMN to DCR conversion:', error);
    throw error;
  }
};