// Ghidra headless postScript：把函数反编译成伪代码写入文件（供 ghidra_decompile 包装器调用）。
// 参数：args[0]=函数名子串（空=全部） args[1]=最多打印多少个函数 args[2]=输出文件路径
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.app.script.GhidraScript;
import ghidra.program.model.listing.Function;
import ghidra.program.model.listing.FunctionManager;
import ghidra.util.task.ConsoleTaskMonitor;

import java.io.PrintWriter;

public class DecompileFunc extends GhidraScript {
    @Override
    public void run() throws Exception {
        String want = getScriptArgs().length > 0 ? getScriptArgs()[0] : "";
        int limit = 40;
        if (getScriptArgs().length > 1) {
            try {
                limit = Integer.parseInt(getScriptArgs()[1]);
            } catch (NumberFormatException e) {
                limit = 40;
            }
        }
        String outPath = getScriptArgs().length > 2 ? getScriptArgs()[2] : "";
        PrintWriter out;
        if (outPath.length() > 0) {
            out = new PrintWriter(outPath, "UTF-8");
        } else {
            out = new PrintWriter(System.out);
        }
        DecompInterface di = new DecompInterface();
        di.toggleCCode(true);
        di.toggleSyntaxTree(true);
        if (!di.openProgram(currentProgram)) {
            out.println("!! 打开程序失败");
            out.flush();
            return;
        }
        FunctionManager fm = currentProgram.getFunctionManager();
        int printed = 0;
        for (Function f : fm.getFunctions(true)) {
            if (f.isThunk()) {
                continue;
            }
            if (want.length() > 0 && !f.getName().toLowerCase().contains(want.toLowerCase())) {
                continue;
            }
            DecompileResults r = di.decompileFunction(f, 90, new ConsoleTaskMonitor());
            if (r == null || !r.decompileCompleted()) {
                out.println("===== " + f.getName() + " (decompile failed) =====");
            } else {
                out.println("===== " + f.getName() + " @ " + f.getEntryPoint() + " =====");
                out.println(r.getDecompiledFunction().getC());
            }
            printed += 1;
            if (printed >= limit) {
                break;
            }
        }
        out.println("===== DONE printed=" + printed + " =====");
        out.flush();
        out.close();
    }
}