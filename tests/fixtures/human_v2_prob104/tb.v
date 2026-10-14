`timescale 1 ps/1 ps

module tb;
    reg clk = 0;
    reg L = 0;
    reg q_in = 1;
    reg r_in = 0;
    wire Q;

    TopModule dut (.clk(clk), .L(L), .q_in(q_in), .r_in(r_in), .Q(Q));

    initial begin
        #1;
        if (Q !== 1'b0) $display("FAIL: Q must have a deterministic initial value");
        #4 clk = 1;
        #1;
        if (Q !== 1'b1) $display("FAIL: q_in was not captured on the first edge");
        $display("Simulation finished");
        $finish;
    end
endmodule
