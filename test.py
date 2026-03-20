from api import acc2match

def test_acc2match():
    # From decode.py and JS selfTest: acc2match(15628582) should equal 63606719
    expected = 63606719
    got = acc2match(15628582)
    if got == expected:
        print(f"[Test] acc2match(15628582) = {got} ✓")
    else:
        print(f"[Test] acc2match MISMATCH: expected {expected}, got {got}")
        exit(1)

if __name__ == '__main__':
    test_acc2match()
