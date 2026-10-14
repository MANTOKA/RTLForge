module tb;
  reg clk = 0;
  reg rst = 1;
  wire [3:0] q;
  reg [3:0] expected = 0;
  integer cycle;

  counter dut(.clk(clk), .rst(rst), .q(q));
  always #5 clk = ~clk;

  initial begin
    @(negedge clk);
    if (q !== 4'd0) $fatal(1, "reset mismatch");
    rst = 0;
    for (cycle = 0; cycle < 18; cycle = cycle + 1) begin
      @(negedge clk);
      expected = expected + 4'd1;
      if (q !== expected) $fatal(1, "counter mismatch at cycle %0d", cycle);
    end
    $display("COUNTER_GOLDEN_PASS");
    $finish;
  end

  initial begin
    #250;
    $fatal(1, "counter timeout");
  end
endmodule
