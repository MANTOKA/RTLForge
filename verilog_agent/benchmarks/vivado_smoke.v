module vivado_smoke(input clk, input rst, input [7:0] d, output reg [7:0] q);
reg [7:0] stage;
always @(posedge clk) begin
  if (rst) begin stage <= 8'd0; q <= 8'd0; end
  else begin stage <= d + 8'd1; q <= stage ^ 8'h5a; end
end
endmodule
