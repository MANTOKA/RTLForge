module tb;
    reg clk = 0;
    always #5 clk = ~clk;
    reg rst = 1;
    wire [3:0] q;
    counter_wrapper dut (.clk(clk), .rst(rst), .q(q));
    integer i;
    reg [3:0] expected;
    initial begin
        repeat (2) @(negedge clk);
        if (q !== 4'd0) $fatal(1, "reset mismatch");
        rst = 0;
        expected = 0;
        for (i = 0; i < 80; i = i + 1) begin
            @(posedge clk); #1;
            expected = expected + 4'd1;
            if (q !== expected) $fatal(1, "increment/wrap mismatch");
        end
        @(negedge clk); rst = 1;
        @(posedge clk); #1;
        if (q !== 0) $fatal(1, "running reset mismatch");
        $display("INTEGRATION_GOLDEN_PASS checks=82");
        $finish;
    end
    initial begin #2000; $fatal(1, "timeout"); end
endmodule
