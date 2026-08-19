`timescale 1ns/1ps

module tb_counter;

reg clk;
reg rst;
wire [3:0] q;

counter uut (
    .clk(clk),
    .rst(rst),
    .q(q)
);

initial begin
    clk = 0;
    forever #5 clk = ~clk;
end

initial begin
    rst = 1;

    #10;
    rst = 0;

    #50;

    $display("Simulation finished");
    $finish;
end

endmodule
