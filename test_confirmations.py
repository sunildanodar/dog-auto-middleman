"""
Test Dynamic Confirmation Requirements
Simple test to verify the confirmation logic works correctly
"""

def test_dynamic_confirmations():
    """Test the dynamic confirmation requirements logic"""
    from core.payment import PaymentProcessor
    from core.state import StateManager
    
    # Create payment processor
    processor = PaymentProcessor(StateManager())
    
    # Test cases: (amount_usd, expected_confirmations)
    test_cases = [
        (50, 1),    # Under $100 -> 1 confirmation
        (99.99, 1), # Under $100 -> 1 confirmation
        (100, 2),   # $100-$500 -> 2 confirmations
        (250, 2),   # $100-$500 -> 2 confirmations
        (500, 2),   # $100-$500 -> 2 confirmations
        (501, 3),   # Above $500 -> 3 confirmations
        (1000, 3),  # Above $500 -> 3 confirmations
        (5000, 3),  # Above $500 -> 3 confirmations
    ]
    
    print("Testing Dynamic Confirmation Requirements:")
    print("=" * 50)
    
    all_passed = True
    
    for amount_usd, expected_confirmations in test_cases:
        actual_confirmations = processor.get_required_confirmations(amount_usd)
        
        status = "PASS" if actual_confirmations == expected_confirmations else "FAIL"
        if status == "FAIL":
            all_passed = False
        
        print(f"${amount_usd:6.2f} -> {actual_confirmations} confirmations (expected {expected_confirmations}) [{status}]")
    
    print("=" * 50)
    
    if all_passed:
        print("All tests PASSED! Dynamic confirmation logic working correctly.")
    else:
        print("Some tests FAILED! Check the logic.")
    
    return all_passed

def test_payment_info_structure():
    """Test that PaymentInfo has the required_confirmations field"""
    from core.payment import PaymentInfo, PaymentStatus
    from datetime import datetime, timezone
    
    print("\nTesting PaymentInfo Structure:")
    print("=" * 50)
    
    # Create a test payment info
    payment = PaymentInfo(
        ticket_id=123,
        crypto="LTC",
        amount_usd=250.0
    )
    
    # Check if required_confirmations field exists
    has_field = hasattr(payment, 'required_confirmations')
    print(f"PaymentInfo has 'required_confirmations' field: {has_field}")
    
    if has_field:
        print(f"Default required_confirmations: {payment.required_confirmations}")
        print("PaymentInfo structure test PASSED!")
    else:
        print("PaymentInfo structure test FAILED!")
    
    return has_field

if __name__ == "__main__":
    # Run tests
    test1_passed = test_dynamic_confirmations()
    test2_passed = test_payment_info_structure()
    
    print("\n" + "=" * 50)
    print("SUMMARY:")
    print(f"Dynamic Confirmation Logic: {'PASS' if test1_passed else 'FAIL'}")
    print(f"PaymentInfo Structure: {'PASS' if test2_passed else 'FAIL'}")
    
    if test1_passed and test2_passed:
        print("\nAll tests PASSED! System is ready for deployment.")
    else:
        print("\nSome tests failed. Review the implementation.")
