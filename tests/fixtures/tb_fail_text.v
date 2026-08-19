module tb_fail_text;
  initial begin
    $display("FAIL: expected and actual values differ");
    $finish;
  end
endmodule
