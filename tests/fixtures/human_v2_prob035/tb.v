`timescale 1ns/1ps

module tb;
    reg clk;
    reg reset;
    wire [3:0] q;

    TopModule dut (
        .clk(clk),
        .reset(reset),
        .q(q)
    );

    initial begin
        clk = 0;
        forever #5 clk = ~clk;
    end

    initial begin
        reset = 1;
        @(posedge clk);
        #1;
        if (q !== 4'd1) $fatal(1, "reset must set q to 1");

        reset = 0;
        repeat (9) @(posedge clk);
        #1;
        if (q !== 4'd10) $fatal(1, "counter must reach 10");

        @(posedge clk);
        #1;
        if (q !== 4'd1) $fatal(1, "counter must wrap to 1");

        $display("Simulation finished");
        $finish;
    end
endmodule
