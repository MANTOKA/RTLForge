module tb_timeout;
  reg clk = 1'b0;
  always #1 clk = ~clk;
endmodule
