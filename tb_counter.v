//这是为DUT(Design Under Test, DUT)创建的一个“测试环境”。它负责产生时钟、复位和各种输入激励信号，
//并将这些信号连接到DUT的输入端口，同时监测DUT的输出，以验证其行为是否正确
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